"""
Grader Agent — grades each submission against the approved rubric
using the Groq API (LLaMA 3.3 70B).

Features:
  - Concurrent grading via ThreadPoolExecutor
  - Per-category rubric breakdown
  - Retry with exponential backoff
  - Cache-aware (skip already-graded files)
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from groq import Groq

from config import MODEL, MAX_CONCURRENT_GRADES
from utils.retry import retry_api_call

logger = logging.getLogger(__name__)


def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def _call_llm(client: Groq, system_prompt: str, user_prompt: str) -> str:
    """Make a single Groq chat completion call (wrapped by retry externally)."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=2048,
    )
    return response.choices[0].message.content.strip()


def _parse_json(raw: str, fallback_name: str) -> dict:
    """Extract JSON from an LLM response, handling code fences, then validate scores."""
    text = raw
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return {
            "name": fallback_name,
            "id": "N/A",
            "marks": "Error",
            "deductions": f"Could not parse LLM response: {raw[:300]}",
            "category_scores": {},
            "feedback": "",
        }

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
                correction = "[Auto-corrected: LLM total did not match category sum]"
                result["deductions"] = f"{deductions} {correction}".strip()

    return result


SYSTEM_PROMPT = (
    "You are an expert academic grader. You will receive a structured JSON "
    "grading rubric and a student submission.\n\n"
    "The rubric contains a 'criteria' array. Each criterion has a 'name', "
    "'max_score', and 'description'.\n\n"
    "Your job is to:\n"
    "1. Extract the student's name and ID from the submission text.\n"
    "2. Score the submission on EACH criterion individually. The score for "
    "each criterion MUST be between 0 and that criterion's max_score.\n"
    "3. Sum the individual criterion scores to produce the total marks.\n"
    "4. List any mark deductions with clear reasons.\n"
    "5. Write a brief 2-3 sentence qualitative feedback summary.\n\n"
    "Respond ONLY with valid JSON in this exact format:\n"
    "{\n"
    '  "name": "...",\n'
    '  "id": "...",\n'
    '  "marks": <total — must equal sum of category_scores>,\n'
    '  "category_scores": {"Criterion Name": <score>, ...},\n'
    '  "deductions": "...",\n'
    '  "feedback": "..."\n'
    "}\n\n"
    "IMPORTANT: 'category_scores' must contain one entry for EVERY criterion "
    "in the rubric, using the exact criterion name as the key. "
    "'marks' must equal the sum of all values in 'category_scores'.\n"
    "If the name or ID cannot be found, use the filename as the name "
    'and "N/A" as the ID.'
)


def grade_submission(rubric: str, submission_text: str, filename: str) -> dict:
    """
    Grade a single student submission with retry logic.

    Returns a dict with keys: name, id, marks, category_scores, deductions, feedback
    """
    client = _get_client()

    user_prompt = (
        f"Grading Rubric:\n{rubric}\n\n"
        f"Submission Filename: {filename}\n\n"
        f"Submission Content:\n{submission_text}"
    )

    raw = retry_api_call(_call_llm, client, SYSTEM_PROMPT, user_prompt)
    return _parse_json(raw, filename)


def grade_all(
    rubric: str,
    submissions: list[dict],
    cached: dict[str, dict] | None = None,
    on_complete=None,
) -> list[dict]:
    """
    Grade every submission concurrently and return a list of result dicts.

    Parameters
    ----------
    rubric : str
        The approved grading rubric.
    submissions : list[dict]
        Each dict has keys: filename, path, content.
    cached : dict[str, dict] | None
        Already-graded results keyed by filename (skip these).
    on_complete : callable(filename, result) | None
        Called after each submission is graded (for cache writes / progress).

    Returns
    -------
    list[dict]  — each with keys: name, id, marks, category_scores, deductions, feedback
    """
    cached = cached or {}
    results: list[dict] = []
    to_grade: list[dict] = []

    # Separate cached vs new
    for sub in submissions:
        if sub["filename"] in cached:
            logger.info("Using cached result for %s", sub["filename"])
            entry = cached[sub["filename"]]
            entry["filename"] = sub["filename"]
            results.append(entry)
        else:
            to_grade.append(sub)

    if not to_grade:
        return results

    total = len(submissions)
    done = len(results)

    def _grade_one(sub: dict) -> dict:
        result = grade_submission(rubric, sub["content"], sub["filename"])
        result["filename"] = sub["filename"]
        return result

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GRADES) as pool:
        future_map = {pool.submit(_grade_one, sub): sub for sub in to_grade}
        for future in as_completed(future_map):
            done += 1
            sub = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.error("Failed to grade %s: %s", sub["filename"], exc)
                result = {
                    "name": sub["filename"],
                    "id": "N/A",
                    "marks": "Error",
                    "category_scores": {},
                    "deductions": f"Grading failed: {exc}",
                    "feedback": "",
                    "filename": sub["filename"],
                }
            results.append(result)
            logger.info("Graded [%d/%d]: %s", done, total, sub["filename"])
            if on_complete:
                on_complete(sub["filename"], result)

    return results
