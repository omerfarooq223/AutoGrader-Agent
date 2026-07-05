"""
Plagiarism Agent — detects similarity between submissions using
TF-IDF cosine similarity + character n-gram overlap, and flags pairs >= threshold.

Production hardening:
  - Skips error/skipped submissions to prevent false flags
  - Minimum content length guard for reliable scoring
  - Consistent cache_key matching with grading results
  - apply_flags no longer mutates input
"""

import logging

from typing import Union, Optional
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)

# Submissions shorter than this are too small for reliable similarity scoring
_MIN_CONTENT_CHARS = 200

# Content prefixes that indicate a failed/skipped read — not real submissions
_ERROR_PREFIXES = ("[ERROR:", "[SKIPPED:")


def _is_gradeable(content: str) -> bool:
    """Return True if content is a real submission, not an error placeholder."""
    if not content:
        return False
    if any(content.startswith(prefix) for prefix in _ERROR_PREFIXES):
        return False
    if len(content) < _MIN_CONTENT_CHARS:
        return False
    return True


def _ngram_jaccard(text_a: str, text_b: str, n: int = 4) -> float:
    """Compute Jaccard similarity on character n-grams."""
    if len(text_a) < n or len(text_b) < n:
        return 0.0
    grams_a = set(text_a[i:i + n] for i in range(len(text_a) - n + 1))
    grams_b = set(text_b[i:i + n] for i in range(len(text_b) - n + 1))
    intersection = grams_a & grams_b
    union = grams_a | grams_b
    return len(intersection) / len(union) if union else 0.0


def _normalize_threshold(threshold: float | int | str | None) -> float:
    """Return a similarity threshold as a 0.0-1.0 ratio."""
    if threshold is None:
        return SIMILARITY_THRESHOLD
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        return SIMILARITY_THRESHOLD
    if value > 1:
        value = value / 100.0
    return max(0.0, min(value, 1.0))


def check_plagiarism(
    submissions: list,
    results: Optional[list] = None,
    threshold: float | int | str | None = None,
) -> dict:
    """
    Compare all submission pairs using a combined similarity score:
      combined = 0.6 * cosine_similarity + 0.4 * ngram_jaccard

    Parameters
    ----------
    submissions : list[dict]
        Each dict must have keys: filename, content, and optionally cache_key.
    results : list[dict] | None
        Optional. The graded results list containing the 'name' field for each student.
    threshold : float | int | str | None
        Similarity threshold for flagging. Accepts a ratio (0.65) or percent (65).
        Defaults to config.SIMILARITY_THRESHOLD.

    Returns
    -------
    dict[str, list[str]]
        Mapping of cache_key -> list of descriptive flag strings.
        Only files involved in a flagged pair appear as keys.
    """
    active_threshold = _normalize_threshold(threshold)

    # Filter to gradeable submissions only — error placeholders skew results
    gradeable = [
        s for s in submissions
        if _is_gradeable(s.get("content", ""))
    ]

    skipped = len(submissions) - len(gradeable)
    if skipped:
        logger.warning(
            "Plagiarism check: skipping %d submission(s) with error/short content.",
            skipped
        )

    if len(gradeable) < 2:
        logger.info("Fewer than 2 gradeable submissions — plagiarism check skipped.")
        return {}

    # Use cache_key as the stable identifier — handles duplicate filenames
    keys     = [s.get("cache_key", s["filename"]) for s in gradeable]
    
    # Map cache_key and filename to student name
    name_map = {}
    if results:
        for r in results:
            student_name = r.get("name")
            if student_name and student_name != "NOT FOUND":
                if r.get("cache_key"):
                    name_map[r.get("cache_key")] = student_name
                if r.get("filename"):
                    name_map[r.get("filename")] = student_name

    names = []
    for s in gradeable:
        k = s.get("cache_key", s["filename"])
        fname = s["filename"]
        student_name = name_map.get(k) or name_map.get(fname) or fname
        names.append(student_name)
        
    contents = [s["content"] for s in gradeable]

    # TF-IDF cosine similarity matrix
    vectorizer   = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(contents)
    cosine_matrix = cosine_similarity(tfidf_matrix)

    flags: dict[str, list[str]] = {}

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            cos_score  = cosine_matrix[i][j]
            ngram_score = _ngram_jaccard(contents[i], contents[j])
            combined   = 0.6 * cos_score + 0.4 * ngram_score

            if combined >= active_threshold:
                pct    = f"{combined * 100:.1f}%"
                detail = f"cos={cos_score:.0%} ngram={ngram_score:.0%}"
                flags.setdefault(keys[i], []).append(
                    f"Similar to {names[j]} ({pct}, {detail})"
                )
                flags.setdefault(keys[j], []).append(
                    f"Similar to {names[i]} ({pct}, {detail})"
                )
                logger.warning(
                    "Plagiarism flag: %s <-> %s — %s (%s)",
                    names[i], names[j], pct, detail,
                )

    logger.info(
        "Plagiarism check complete: %d pair(s) flagged out of %d comparisons.",
        sum(len(v) for v in flags.values()) // 2,
        len(keys) * (len(keys) - 1) // 2,
    )
    return flags


def apply_flags(results: list[dict], flags: dict[str, list[str]]) -> list[dict]:
    """
    Return a new list of result dicts with plagiarism_flag added.
    Matches on cache_key for consistency with grading results.
    Does NOT mutate the input list.
    """
    updated = []
    for entry in results:
        new_entry = dict(entry)
        key     = entry.get("cache_key", entry.get("filename", ""))
        matched = flags.get(key, [])
        new_entry["plagiarism_flag"] = " | ".join(matched) if matched else ""
        updated.append(new_entry)
    return updated


def apply_flags_and_penalty(
    results: list[dict],
    flags: dict[str, list[str]],
    penalty_marks: float | int | str | None = 0,
) -> list[dict]:
    """
    Return results with plagiarism flags and an optional one-time mark penalty.

    The penalty is applied once per flagged student, not once per matched pair.
    This keeps policy predictable when a submission is similar to multiple files.
    """
    try:
        penalty = max(0.0, float(penalty_marks or 0))
    except (TypeError, ValueError):
        penalty = 0.0

    updated = []
    for entry in apply_flags(results, flags):
        new_entry = dict(entry)
        if penalty and new_entry.get("plagiarism_flag") and isinstance(new_entry.get("marks"), (int, float)):
            original = new_entry["marks"]
            adjusted = max(0, original - penalty)
            new_entry["marks"] = int(adjusted) if adjusted == int(adjusted) else adjusted
            penalty_display = int(penalty) if penalty == int(penalty) else penalty
            existing = new_entry.get("deductions", "") or ""
            penalty_text = f"Plagiarism policy: similarity flag penalty (-{penalty_display})"
            new_entry["deductions"] = (
                penalty_text
                if not existing or existing == "No deductions."
                else f"{existing}, {penalty_text}"
            )
            new_entry["plagiarism_penalty"] = penalty_display
            new_entry["plagiarism_original_marks"] = original
        else:
            new_entry["plagiarism_penalty"] = 0
        updated.append(new_entry)
    return updated
