"""
Plagiarism Agent — detects similarity between submissions using
TF-IDF cosine similarity + character n-gram overlap, and flags pairs ≥ threshold.

Features:
  - Dual detection: TF-IDF cosine similarity + character-level n-gram Jaccard overlap
  - Combined score for more robust detection
  - Configurable threshold from config
"""

import logging

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)


def _ngram_jaccard(text_a: str, text_b: str, n: int = 4) -> float:
    """Compute Jaccard similarity on character n-grams."""
    if len(text_a) < n or len(text_b) < n:
        return 0.0
    grams_a = set(text_a[i:i + n] for i in range(len(text_a) - n + 1))
    grams_b = set(text_b[i:i + n] for i in range(len(text_b) - n + 1))
    intersection = grams_a & grams_b
    union = grams_a | grams_b
    return len(intersection) / len(union) if union else 0.0


def check_plagiarism(submissions: list[dict]) -> dict[str, list[str]]:
    """
    Compare all submission pairs using a combined similarity score:
      combined = 0.6 * cosine_similarity + 0.4 * ngram_jaccard

    Parameters
    ----------
    submissions : list[dict]
        Each dict must have keys: filename, content.

    Returns
    -------
    dict[str, list[str]]
        Mapping of filename → list of descriptive flag strings.
        Only files involved in a flagged pair appear as keys.
    """
    if len(submissions) < 2:
        return {}

    filenames = [s["filename"] for s in submissions]
    contents = [s["content"] for s in submissions]

    # TF-IDF cosine similarity matrix
    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(contents)
    cosine_matrix = cosine_similarity(tfidf_matrix)

    flags: dict[str, list[str]] = {}

    for i in range(len(filenames)):
        for j in range(i + 1, len(filenames)):
            cos_score = cosine_matrix[i][j]
            ngram_score = _ngram_jaccard(contents[i], contents[j])
            combined = 0.6 * cos_score + 0.4 * ngram_score

            if combined >= SIMILARITY_THRESHOLD:
                pct = f"{combined * 100:.1f}%"
                detail = f"cos={cos_score:.0%} ngram={ngram_score:.0%}"
                msg_i = f"Similar to {filenames[j]} ({pct}, {detail})"
                msg_j = f"Similar to {filenames[i]} ({pct}, {detail})"
                flags.setdefault(filenames[i], []).append(msg_i)
                flags.setdefault(filenames[j], []).append(msg_j)
                logger.warning(
                    "Plagiarism flag: %s ↔ %s — %s (%s)",
                    filenames[i], filenames[j], pct, detail,
                )

    logger.info(
        "Plagiarism check complete: %d pair(s) flagged out of %d comparisons.",
        sum(len(v) for v in flags.values()) // 2,
        len(filenames) * (len(filenames) - 1) // 2,
    )
    return flags


def apply_flags(results: list[dict], flags: dict[str, list[str]]) -> list[dict]:
    """
    Merge plagiarism flags into grading results in-place and return them.
    Adds a 'plagiarism_flag' key to each result dict.
    """
    for entry in results:
        fname = entry.get("filename", "")
        matched = flags.get(fname, [])
        entry["plagiarism_flag"] = " | ".join(matched) if matched else ""
    return results
