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

from groq import Groq

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


def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def _call_llm(client: Groq, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Make a single Groq chat completion call."""
    raw = call_llm(system_prompt, user_prompt, max_tokens=max_tokens)
    return raw


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


def _parse_json(raw: str, fallback_name: str) -> dict:
    """
    Extract JSON from an LLM response, handle code fences, validate scores,
    and clean the student name field.
    """
    text = raw
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return {
            "name":             "NOT FOUND",
            "id":               "NOT FOUND",
            "marks":            "Error",
            "deductions":       f"Could not parse LLM response: {raw[:300]}",
            "category_scores":  {},
            "feedback":         "",
        }

    # Clean name — remove filename artifacts
    result["name"] = _clean_name(
        result.get("name", ""), fallback_name
    )

    # Clean ID — if it looks like a filename or is empty, mark as NOT FOUND
    raw_id = str(result.get("id", "")).strip()
    if not raw_id or raw_id.lower() in ("n/a", "not found", "none", ""):
        result["id"] = "NOT FOUND"
    else:
        result["id"] = raw_id

    # Validate: category_scores sum must match marks
    cat_scores = result.get("category_scores", {})
    if cat_scores and isinstance(cat_scores, dict):
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
    "3. Score the submission on EACH criterion. Score must be 0 to max_score.\n"
    "4. Sum criterion scores to get total marks.\n"
    "5. List mark deductions with specific reasons.\n\n"
    "Respond ONLY with valid JSON — no markdown, no extra text:\n"
    "{\n"
    '  "name": "<student full name or NOT FOUND>",\n'
    '  "id": "<student ID or NOT FOUND>",\n'
    '  "marks": <total — must equal sum of category_scores>,\n'
    '  "category_scores": {"Criterion Name": <score>, ...},\n'
    '  "deductions": "...",\n'
    '  "feedback": "..."\n'
    "}\n\n"
    "CRITICAL: 'name' must be ONLY the student's name — never a filename, "
    "never an ID, never a path. "
    "'marks' must equal the exact sum of all values in 'category_scores'."
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
    client = _get_client()

    if answer_key:
        rubric_text = _trim_text(rubric, MAX_RUBRIC_CHARS, "Rubric")
        answer_key_text = _trim_text(answer_key, MAX_ANSWER_KEY_CHARS, "Answer key")
        submission_for_llm = _trim_text(submission_text, MAX_SUBMISSION_CHARS, "Submission")
        prompt = (
            "Compare this student submission to the provided answer key "
            "and grade using the rubric.\n\n"
            f"Grading Rubric:\n{rubric_text}\n\n"
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
            f"Submission Filename: {filename}\n\n"
            f"Submission Content:\n{submission_for_llm}"
        )

    raw = retry_api_call(
        _call_llm,
        client,
        SYSTEM_PROMPT,
        prompt,
        cancel_event=cancel_event,
        max_tokens=GRADING_MAX_OUTPUT_TOKENS,
    )
    return _parse_json(raw, filename)


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
        if sub["filename"] in cached:
            logger.info("Using cached result for %s", sub["filename"])
            entry = dict(cached[sub["filename"]])
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
        # Since MAX_CONCURRENT_GRADES=1 and grading itself takes time, 1.5s is safe.
        time.sleep(1.5)
        
        result = grade_submission(
            rubric,
            sub["content"],
            sub["filename"],
            answer_key=answer_key,
            cancel_event=cancel_event,
        )
        result["filename"] = sub["filename"]
        return result

    pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GRADES)
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