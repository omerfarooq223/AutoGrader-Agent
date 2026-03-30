"""
Grader Agent — grades each submission against the approved rubric
using the Groq API (LLaMA 3.3 70B).

Features:
  - Concurrent grading via ThreadPoolExecutor
  - Per-category rubric breakdown with score validation
  - Retry with exponential backoff
  - Cache-aware (skip already-graded files)
  - Answer key support for comparison-based grading
"""

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    MODEL,
    MAX_CONCURRENT_GRADES,
    GRADING_MAX_OUTPUT_TOKENS,
    MAX_SUBMISSION_CHARS,
    MAX_ANSWER_KEY_CHARS,
    MAX_RUBRIC_CHARS,
)
from utils.retry import retry_api_call
from utils.llm_client import call_llm

logger = logging.getLogger(__name__)


def _trim_text(text: str, max_chars: int, label: str) -> str:
    """
    Trim long prompt sections to reduce token spend on free-tier plans.
    Keeps both head and tail because conclusions often appear at the end.
    """
    # max_chars <= 0 disables truncation.
    if max_chars <= 0:
        return text
    if not text or len(text) <= max_chars:
        return text
    if max_chars < 200:
        return text[:max_chars]
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return (
        f"{text[:head]}\n\n"
        f"[{label} truncated to save tokens. {len(text) - max_chars} chars omitted.]\n\n"
        f"{text[-tail:]}"
    )




def _call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Make a single Groq chat completion call."""
    return call_llm(system_prompt, user_prompt, max_tokens=max_tokens)


def _clean_name(raw_name: str, filename: str) -> str:
    """
    Clean extracted student name:
    - Strip file extensions, IDs, and extra tokens that look like filenames
    - Return 'NOT FOUND' if the name looks like a filename or ID
    """
    if not raw_name or raw_name in ("", "N/A", "NOT FOUND"):
        return "NOT FOUND"

    # If it looks like a filename (has extension), strip it
    name = re.sub(r'\.(docx?|pdf|py|cpp|ipynb|txt)$', '', raw_name, flags=re.IGNORECASE).strip()

    # Remove common ID patterns that leak into the name field (e.g. F2023376425)
    name = re.sub(r'\b[A-Z]{1,3}\d{6,}\b', '', name).strip()

    # Remove leading/trailing underscores, hyphens, digits
    name = re.sub(r'^[\s_\-\d]+|[\s_\-\d]+$', '', name).strip()

    # If what's left is empty or looks like a path/filename, return NOT FOUND
    if not name or '/' in name or '\\' in name or name.lower() == filename.lower():
        return "NOT FOUND"

    return name


def _parse_json(raw: str, fallback_name: str, rubric: str = None) -> dict:
    """
    Extract JSON from an LLM response, handle code fences, validate scores,
    enforce per-criterion max_score caps, and clean the student name field.
    """
    text = raw
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try brace-matching to extract partial JSON before giving up
        start = text.find("{")
        recovered = None
        if start != -1:
            depth, end = 0, start
            for i, ch in enumerate(text[start:], start):
                if ch == "{": depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            try:
                recovered = json.loads(text[start:end + 1])
            except Exception:
                pass
        if recovered is None:
            return {
                "name":            fallback_name,
                "id":              fallback_name,
                "marks":           "Error",
                "deductions":      f"Could not parse LLM response: {raw[:300]}",
                "category_scores": {},
                "feedback":        "",
            }
        result = recovered

    # Clean name — use filename as fallback if name not found in submission
    cleaned_name = _clean_name(result.get("name", ""), fallback_name)
    result["name"] = cleaned_name if cleaned_name != "NOT FOUND" else fallback_name

    # Normalize deductions — LLM sometimes returns a list instead of a string
    deductions = result.get("deductions", "")
    if isinstance(deductions, list):
        result["deductions"] = " ".join(str(d) for d in deductions)
    elif deductions is None:
        result["deductions"] = ""

    # Clean ID — use filename as fallback if ID not found in submission
    raw_id = str(result.get("id", "")).strip()
    if not raw_id or raw_id.lower() in ("n/a", "not found", "none", ""):
        result["id"] = fallback_name
    else:
        result["id"] = raw_id

    # ── Post-process: apply deductions written in text to actual scores ──
    # The LLM often writes "CriterionName: reason (-N)" but forgets to subtract.
    # Parse deduction text and enforce it against category_scores non-LLM style.
    deduction_text = result.get("deductions", "") or ""
    cat_scores_raw = result.get("category_scores", {})
    if deduction_text and cat_scores_raw and isinstance(cat_scores_raw, dict):
        import re as _re
        # Match patterns like "Mutual Exclusion: some reason (-3)"
        # or "Deadlock Detection: explanation (-1)"
        deduction_pattern = _re.compile(
            r'([A-Za-z][A-Za-z\s]+?):\s+[^(]*\(-\s*(\d+(?:\.\d+)?)\)',
            _re.IGNORECASE
        )
        # Accumulate total deductions per criterion name
        criterion_deductions: dict[str, float] = {}
        for match in deduction_pattern.finditer(deduction_text):
            crit_raw = match.group(1).strip()
            amount   = float(match.group(2))
            # Find closest matching key in category_scores
            matched_key = None
            for k in cat_scores_raw:
                if crit_raw.lower() in k.lower() or k.lower() in crit_raw.lower():
                    matched_key = k
                    break
            if matched_key:
                criterion_deductions[matched_key] = (
                    criterion_deductions.get(matched_key, 0) + amount
                )
        # Apply deductions to category scores
        if criterion_deductions:
            rubric_maxes: dict[str, float] = {}
            if rubric:
                try:
                    import json as _j
                    robj = _j.loads(rubric) if isinstance(rubric, str) else rubric
                    for c in robj.get("criteria", []):
                        rubric_maxes[c["name"]] = float(c.get("max_score", 100))
                except Exception:
                    pass
            adjusted = False
            for k, deduct in criterion_deductions.items():
                if k in cat_scores_raw and isinstance(cat_scores_raw[k], (int, float)):
                    cap = rubric_maxes.get(k, 100)
                    new_score = max(0.0, min(cap, float(cat_scores_raw[k]) - deduct))
                    if new_score != cat_scores_raw[k]:
                        cat_scores_raw[k] = new_score
                        adjusted = True
            if adjusted:
                result["category_scores"] = cat_scores_raw
                new_total = sum(
                    v for v in cat_scores_raw.values()
                    if isinstance(v, (int, float))
                )
                result["marks"] = new_total

    # Validate: cap individual scores to their max_score, then fix total
    cat_scores = result.get("category_scores", {})
    if cat_scores and isinstance(cat_scores, dict):
        # Build max_score lookup from rubric to enforce per-criterion caps
        max_scores: dict[str, float] = {}
        if rubric:
            try:
                import json as _json
                rubric_obj = _json.loads(rubric) if isinstance(rubric, str) else rubric
                for criterion in rubric_obj.get("criteria", []):
                    max_scores[criterion["name"]] = float(criterion.get("max_score", 100))
            except Exception:
                pass

        corrections = []
        for k, v in cat_scores.items():
            if not isinstance(v, (int, float)):
                continue
            cap = max_scores.get(k, 100)
            if v > cap:
                cat_scores[k] = cap
                corrections.append(f"{k} capped at {cap}")

        if corrections:
            deductions = result.get("deductions", "") or ""
            note = "[Score capped to rubric max: " + ", ".join(corrections) + "]"
            result["deductions"] = f"{deductions} {note}".strip()

        numeric_scores = [v for v in cat_scores.values() if isinstance(v, (int, float))]
        if numeric_scores:
            correct_sum = sum(numeric_scores)
            marks = result.get("marks")
            if isinstance(marks, (int, float)) and marks != correct_sum:
                result["marks"] = correct_sum
                deductions = result.get("deductions", "") or ""
                correction = "[Score adjusted to match rubric total]"
                result["deductions"] = f"{deductions} {correction}".strip()

    return result


SYSTEM_PROMPT = (
    "You are an expert academic grader. You will receive a structured JSON "
    "grading rubric and a student submission.\n\n"
    "The rubric contains a 'criteria' array. Each criterion has 'name', "
    "'max_score', and 'description'.\n\n"
    "Your job is to:\n"
    "1. Extract the student's FULL NAME from the submission content. "
    "Look for patterns like 'Name:', 'Student:', 'Submitted by:', or a name "
    "written at the top of the document. Return ONLY the name — no IDs, no "
    "filenames, no extra text. If you cannot find a name, return 'NOT FOUND'.\n"
    "2. Extract the student ID. Look for patterns like 'ID:', 'Roll No:', "
    "'Registration:', or alphanumeric codes like 'F2021-CS-045'. "
    "If not found, return 'NOT FOUND'. Do NOT guess or invent an ID.\n"
    "3. Score EACH criterion from 0 to its max_score. "
    "NEVER exceed max_score for any criterion. "
    "If work is flawed, deduct from the score — do NOT give max_score and then "
    "list deductions separately. The score IS the verdict.\n"
    "4. Sum criterion scores to get total marks.\n"
    "5. For each mark deducted, write ONE line in this exact format: "
    "'CriterionName: reason in 4-6 words (-N marks)'. "
    "Example: 'Deadlock Detection: vague RAG explanation (-1)'. "
    "If no marks deducted anywhere, write 'No deductions.' "
    "Never write paragraphs. Never explain what was correct.\n\n"
    "For EACH criterion, before scoring ask yourself: does this submission "
    "FULLY satisfy every requirement for the highest band? If there is ANY "
    "error, vagueness, or omission — even minor — you MUST deduct at least 1 mark. "
    "A score of max_score means the work is flawless for that criterion.\n\n"
    "Respond ONLY with this exact JSON — no markdown, no extra fields:\n"
    "{\n"
    '  "name": "<student full name or NOT FOUND>",\n'
    '  "id": "<student ID or NOT FOUND>",\n'
    '  "marks": <total — must equal sum of category_scores>,\n'
    '  "category_scores": {"Criterion Name": <score>, ...},\n'
    '  "deductions": "<list only actual deductions, or No deductions.>"\n'
    "}\n\n"
    "CRITICAL RULES:\n"
    "- 'name' must be ONLY the student name — never a filename, ID, or path.\n"
    "- Each category score must be between 0 and its max_score. Never exceed max_score.\n"
    "- 'marks' must equal the exact sum of all category_scores.\n"
    "- Do NOT include a 'feedback' field or any field not listed above.\n"
    "- If score is max_score for a criterion, deductions for it must be empty."
)


def grade_submission(
    rubric: str,
    submission_text: str,
    filename: str,
    answer_key: str = None,
    cancel_event: threading.Event = None,
) -> dict:
    """
    Grade a single student submission with retry logic.
    If answer_key is provided, the LLM compares the submission to it.
    """
    # Build allowed scores note from rubric bands — forces discrete grading
    def _build_allowed_scores(rubric_str: str) -> str:
        try:
            import json as _j, re as _r
            obj = _j.loads(rubric_str)
            lines = ["ALLOWED SCORES PER CRITERION (pick ONLY one of these exact values):"]
            for c in obj.get("criteria", []):
                name  = c.get("name", "")
                max_s = int(c.get("max_score", 0))
                desc  = c.get("description", "")
                nums  = sorted(set(
                    [int(x) for x in _r.findall(r"\[(\d+)\s*[Mm]arks?\]", desc)] + [0]
                ), reverse=True)
                if not nums or nums == [0]:
                    nums = list(range(max_s, -1, -1))
                lines.append(f"  {name}: {nums}")
            return "\n".join(lines)
        except Exception:
            return ""

    allowed_scores_note = _build_allowed_scores(rubric)

    if answer_key:
        logger.warning("DEBUG: Answer key present — length %d chars", len(answer_key))
        rubric_text = _trim_text(rubric, MAX_RUBRIC_CHARS, "Rubric")
        answer_key_text = _trim_text(answer_key, MAX_ANSWER_KEY_CHARS, "Answer key")
        submission_for_llm = _trim_text(submission_text, MAX_SUBMISSION_CHARS, "Submission")
        prompt = (
            "Compare this student submission to the provided answer key "
            "and grade using the rubric.\n\n"
            f"Grading Rubric:\n{rubric_text}\n\n"
            f"{allowed_scores_note}\n\n"
            f"Answer Key / Model Solution:\n{answer_key_text}\n\n"
            f"Submission Filename: {filename}\n\n"
            f"Submission Content:\n{submission_for_llm}"
        )
    else:
        rubric_text = _trim_text(rubric, MAX_RUBRIC_CHARS, "Rubric")
        submission_for_llm = _trim_text(submission_text, MAX_SUBMISSION_CHARS, "Submission")
        prompt = (
            "Grade this student submission using the rubric only.\n\n"
            f"Grading Rubric:\n{rubric_text}\n\n"
            f"{allowed_scores_note}\n\n"
            f"Submission Filename: {filename}\n\n"
            f"Submission Content:\n{submission_for_llm}"
        )

    # Guard: if submission is empty (e.g. scanned PDF with no text layer),
    # return a clean error instead of sending empty content to LLM
    if not submission_for_llm or len(submission_for_llm.strip()) < 50:
        return {
            "name":            filename,
            "id":              filename,
            "marks":           "Error",
            "category_scores": {},
            "deductions":      "[No readable text found — likely a scanned image. Enable EXTRACT_IMAGES=True or convert to searchable PDF.]",
            "feedback":        "",
        }

    raw = retry_api_call(
        _call_llm,
        SYSTEM_PROMPT,
        prompt,
        cancel_event=cancel_event,
        max_tokens=GRADING_MAX_OUTPUT_TOKENS,
    )
    return _parse_json(raw, filename, rubric=rubric)


def grade_all(
    rubric: str,
    submissions: list[dict],
    cached: dict[str, dict] | None = None,
    on_complete=None,
    answer_key: str = None,
) -> list[dict]:
    """
    Grade every submission concurrently and return a list of result dicts.

    Parameters
    ----------
    rubric      : The approved grading rubric (JSON string).
    submissions : List of dicts with keys: filename, path, content.
    cached      : Already-graded results keyed by filename (skipped).
    on_complete : Callback(filename, result) after each grading (for progress).
    answer_key  : Optional model answer to compare submissions against.

    Returns
    -------
    list[dict] — each with keys: name, id, marks, category_scores, deductions, feedback
    """
    cached  = cached or {}
    results: list[dict] = []
    to_grade: list[dict] = []

    for sub in submissions:
        if sub.get("cache_key", sub["filename"]) in cached:
            logger.info("Using cached result for %s", sub["filename"])
            entry = dict(cached[sub.get("cache_key", sub["filename"])])
            entry["filename"] = sub["filename"]
            results.append(entry)
        else:
            to_grade.append(sub)

    if not to_grade:
        return results

    total = len(submissions)
    done  = len(results)

    pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GRADES)
    cancel_event = threading.Event()

    import time
    def _grade_one(sub: dict) -> dict:
        if cancel_event.is_set():
            raise InterruptedError("Cancelled")
        
        # Artificially throttle API calls to prevent rate limits.
        # Since MAX_CONCURRENT_GRADES=1, a 20.0s sleep gives ~3 requests per minute.
        # For long assignments (5+ pages), we increase the delay to 40s to ensure 
        # the Token-Per-Minute quota replenishes fully.
        # llama-3.1-8b-instant has ~5x higher TPM than 70B — shorter sleep is safe
        base_sleep = 30.0
        if len(sub["content"]) > 5000:
            base_sleep = 60.0
            logger.info("Large submission detected (>5k chars). Sleeping 60s for quota recovery.")
        
        time.sleep(base_sleep)
        
        result = grade_submission(
            rubric,
            sub["content"],
            sub["filename"],
            answer_key=answer_key,
            cancel_event=cancel_event,
        )
        result["filename"] = sub["filename"]
        result["cache_key"] = sub.get("cache_key", sub["filename"])
        return result

    future_map = {pool.submit(_grade_one, sub): sub for sub in to_grade}
    
    try:
        for future in as_completed(future_map):
            done += 1
            sub = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.error("Failed to grade %s: %s", sub["filename"], exc)
                result = {
                    "name":            sub["filename"],
                    "id":              "N/A",
                    "marks":           "Error",
                    "category_scores": {},
                    "deductions":      f"Grading failed: {exc}",
                    "feedback":        "",
                    "filename":        sub["filename"],
                }
            results.append(result)
            logger.info("Graded [%d/%d]: %s", done, total, sub["filename"])
            if on_complete:
                on_complete(sub["filename"], result)
    finally:
        # Ensures that if Streamlit interrupts this via a button click (Stop Grading),
        # it doesn't freeze waiting for pending futures to finish, and cascades the termination into sleep loops.
        cancel_event.set()
        pool.shutdown(wait=False, cancel_futures=True)

    return results