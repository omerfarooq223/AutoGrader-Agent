"""
Grader Agent — grades each submission against the approved rubric
using the LLM (Groq primary, Gemini fallback).

Features:
  - Concurrent grading via ThreadPoolExecutor
  - Deterministic Python scoring (totals, deductions, capping)
  - Per-category rubric breakdown with criterion name validation
  - Retry with exponential backoff
  - Cache-aware (skip already-graded files)
  - Answer key support for comparison-based grading
"""

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    MAX_CONCURRENT_GRADES,
    GRADING_MAX_OUTPUT_TOKENS,
    MAX_SUBMISSION_CHARS,
    MAX_ANSWER_KEY_CHARS,
    MAX_RUBRIC_CHARS,
)
from utils.retry import retry_api_call
from utils.llm_client import call_llm

logger = logging.getLogger(__name__)


def _call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Make a single Groq chat completion call."""
    return call_llm(system_prompt, user_prompt, max_tokens=max_tokens)


def _build_rubric_maxes(rubric: str | dict | None) -> dict[str, float]:
    """Parse the rubric to get max_score per criterion name."""
    if not rubric:
        return {}
    try:
        obj = json.loads(rubric) if isinstance(rubric, str) else rubric
        return {
            c["name"]: float(c.get("max_score", 100))
            for c in obj.get("criteria", [])
        }
    except Exception:
        return {}


def _match_criterion_name(llm_name: str, rubric_names: list[str]) -> str | None:
    """
    Fuzzy-match an LLM-returned criterion name to the closest rubric criterion.
    Returns the matched rubric name, or None if no reasonable match found.
    """
    llm_lower = llm_name.strip().lower()

    # 1. Exact match
    for rn in rubric_names:
        if rn.lower() == llm_lower:
            return rn

    # 2. Substring match (either direction)
    for rn in rubric_names:
        if llm_lower in rn.lower() or rn.lower() in llm_lower:
            return rn

    # 3. Word overlap — match if ≥50% of rubric criterion words appear
    llm_words = set(llm_lower.split())
    best_match, best_overlap = None, 0.0
    for rn in rubric_names:
        rn_words = set(rn.lower().split())
        if not rn_words:
            continue
        overlap = len(llm_words & rn_words) / len(rn_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = rn
    if best_overlap >= 0.5:
        return best_match

    return None


def _validate_json_structure(obj: dict, rubric: str | None) -> tuple[bool, str]:
    """
    Validate LLM response JSON structure.
    Returns (is_valid, error_message).
    """
    if not isinstance(obj, dict):
        return False, "Response is not a JSON object"
    
    if "category_scores" not in obj or not isinstance(obj["category_scores"], dict):
        return False, "Missing or invalid 'category_scores' field"
    
    max_scores = _build_rubric_maxes(rubric)
    rubric_names = set(max_scores.keys())
    
    # Only check for empty category_scores if we have a rubric
    # If no rubric, we can't validate, so allow it
    if rubric and not obj["category_scores"]:
        return False, "category_scores is empty — LLM returned no scores"
    
    # Validate scores are numeric or have score field
    for k, v in obj["category_scores"].items():
        if isinstance(v, dict):
            if "score" not in v:
                return False, f"Criterion '{k}' missing 'score' field"
            try:
                float(v["score"])
            except (ValueError, TypeError):
                return False, f"Criterion '{k}' score is not numeric: {v['score']}"
        elif isinstance(v, (int, float)):
            try:
                float(v)
            except (ValueError, TypeError):
                return False, f"Criterion '{k}' score is not numeric: {v}"
        else:
            return False, f"Criterion '{k}' has invalid value type: {type(v)}"
    
    return True, ""


def _parse_json(
    raw: str,
    fallback_name: str,
    rubric: str = None,
    preferred_name: str = "",
    preferred_id: str = "",
) -> dict:
    """
    Extract JSON from an LLM response, then apply deterministic Python
    logic to compute totals, cap scores, and build deduction text.

    The LLM returns per-criterion scores and reasons.  ALL math is done here:
      - Fuzzy-match criterion names to rubric
      - Cap each score to its max_score
      - Fill missing criteria with score 0
      - Sum scores → total marks
      - Build deduction text from (max_score − score) per criterion
    """
    text = raw
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result = None
    parse_error = None
    
    # Step 1: Try direct JSON parsing
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        parse_error = str(e)
        logger.warning("Direct JSON parse failed: %s", parse_error)
    
    # Step 2: Try brace-matching to extract partial JSON
    if result is None:
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
                result = recovered
                logger.info("Recovered partial JSON via brace-matching")
            except Exception as e:
                logger.warning("Brace-matching failed: %s", e)
                parse_error = str(e)
    
    # Step 3: Validate structure if we have a result
    if result is not None:
        is_valid, validation_error = _validate_json_structure(result, rubric)
        if not is_valid:
            logger.warning("JSON structure validation failed: %s", validation_error)
            result = None  # Force error return
    
    # Step 4: If all parsing failed, return structured error
    if result is None:
        error_msg = parse_error or "Unknown parsing error"
        logger.error(
            "Could not parse LLM response. Error: %s. Raw response: %s",
            error_msg, raw[:500]
        )
        return {
            "name":            preferred_name or fallback_name,
            "id":              preferred_id or fallback_name,
            "marks":           "Error",
            "deductions":      f"[LLM Response Parsing Error: {error_msg}]",
            "category_scores": {},
        }

    # ── Name: prefer extractor-verified identity, then parsed name fallback ──
    if preferred_name:
        result["name"] = preferred_name
    else:
        raw_name = result.get("name", "") or ""
        cleaned = re.sub(r'\.(docx?|pdf|py|cpp|ipynb|txt)$', '', raw_name, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'\b[A-Z]{1,3}\d{6,}\b', '', cleaned).strip()
        cleaned = re.sub(r'^[\s_\-\d]+|[\s_\-\d]+$', '', cleaned).strip()
        result["name"] = cleaned if cleaned else fallback_name

    # ── ID: prefer extractor-verified identity, then LLM/content fallback ──
    raw_id = str(result.get("id", "")).strip().upper()
    if preferred_id:
        result["id"] = preferred_id
    elif not raw_id or raw_id.lower() in ("n/a", "not found", "none", ""):
        result["id"] = fallback_name
    else:
        result["id"] = raw_id

    # ── Deterministic scoring — ALL math done in Python ──
    max_scores = _build_rubric_maxes(rubric)
    rubric_names = list(max_scores.keys())
    cat_scores_raw = result.get("category_scores", {})

    # Normalize category_scores — handle both formats:
    #   New format: {"CritName": {"score": N, "reason": "..."}, ...}
    #   Old format (cached): {"CritName": N, ...}
    cat_scores: dict[str, float] = {}
    cat_reasons: dict[str, str] = {}

    for k, v in cat_scores_raw.items():
        if isinstance(v, dict):
            score = v.get("score", 0)
            reason = v.get("reason", "") or ""
        elif isinstance(v, (int, float)):
            score = v
            reason = ""
        else:
            continue

        # Fuzzy-match returned criterion names to rubric criterion names.
        matched_name = _match_criterion_name(k, rubric_names) if rubric_names else None
        canonical_name = matched_name or k

        # Cap score to max_score (never exceed rubric max, never go below 0)
        cap = max_scores.get(canonical_name, 100)
        score = max(0.0, min(float(score), cap))
        cat_scores[canonical_name] = score
        cat_reasons[canonical_name] = reason.strip()

    # Validate: ensure ALL rubric criteria have scores — fill missing with 0
    omitted_criteria = []
    for rubric_crit, rubric_max in max_scores.items():
        if rubric_crit not in cat_scores:
            cat_scores[rubric_crit] = 0.0
            cat_reasons[rubric_crit] = "criterion not evaluated by grader"
            omitted_criteria.append(rubric_crit)
            logger.error("CRITICAL: LLM omitted criterion '%s' — assigning 0 (max: %s). This submission may be under-graded.", rubric_crit, rubric_max)
    
    if omitted_criteria:
        logger.error(
            "WARNING: Submission '%s' has %d omitted criteria: %s. "
            "Consider regenerating the rubric or retrying grading.",
            fallback_name, len(omitted_criteria), ", ".join(omitted_criteria)
        )

    # Backfill reasons from legacy deductions text for cached older results.
    if not any(cat_reasons.values()):
        old_deductions = result.get("deductions", "") or ""
        if isinstance(old_deductions, list):
            old_deductions = ", ".join(str(d) for d in old_deductions)
        for match in re.finditer(
            r'([A-Za-z][A-Za-z\s]+?):\s+([^(,;|]+?)\s*\(-\s*\d+(?:\.\d+)?\)',
            old_deductions,
        ):
            crit_raw = match.group(1).strip()
            reason_text = match.group(2).strip()
            for k in cat_scores:
                if crit_raw.lower() in k.lower() or k.lower() in crit_raw.lower():
                    if not cat_reasons.get(k):
                        cat_reasons[k] = reason_text
                    else:
                        cat_reasons[k] += f", {reason_text}"
                    break

    # Compute total marks in Python.
    if cat_scores:
        total_float = sum(cat_scores.values())
        # Display as int when all scores are whole numbers (13.0 → 13)
        total_marks = int(total_float) if total_float == int(total_float) else total_float
    else:
        total_marks = result.get("marks", "Error")

    # Build deduction text — pure Python
    # Format: "CriterionName: reason (-N)" for each criterion where score < max
    deduction_parts: list[str] = []
    for k, score in cat_scores.items():
        cap = max_scores.get(k, 100)
        deducted = cap - score
        if deducted > 0:
            reason = cat_reasons.get(k, "marks deducted")
            if not reason:
                reason = "marks deducted"
            ded_display = int(deducted) if deducted == int(deducted) else deducted
            deduction_parts.append(f"{k}: {reason} (-{ded_display})")

    deductions_str = ", ".join(deduction_parts) if deduction_parts else "No deductions."

    result["category_scores"] = cat_scores
    result["marks"] = total_marks
    result["deductions"] = deductions_str
    result.pop("feedback", None)  # Remove unused legacy field

    return result


SYSTEM_PROMPT = (
    "You are a STRICT academic grader. You will receive a structured JSON "
    "grading rubric, a student submission, and possibly an answer key.\n\n"
    "The rubric contains a 'criteria' array. Each criterion has 'name', "
    "'max_score', and 'description'.\n\n"
    "GRADING PROCESS — follow these steps IN ORDER:\n"
    "1. Extract the student ID from the submission content. "
    "Look for patterns like 'ID:', 'Roll No:', 'Registration:', "
    "or alphanumeric codes like 'F2023376425'. "
    "If not found, return 'NOT FOUND'. Do NOT guess or invent an ID.\n"
    "2. For EACH criterion, FIRST identify ALL flaws, errors, omissions, "
    "and vague statements in the submission. Compare against the answer key "
    "if provided. List what is WRONG or MISSING before deciding the score.\n"
    "3. THEN assign a score based on the flaws found. "
    "If you found ANY flaw, the score MUST be less than max_score.\n"
    "4. Write a reason for EVERY criterion — explain what the student did "
    "or what they missed. This is REQUIRED even for full marks.\n\n"
    "SCORING RULES:\n"
    "- A score of max_score means ZERO flaws — the work is perfect for that criterion.\n"
    "- If the explanation is vague, generic, or lacks specifics → deduct.\n"
    "- If key concepts are missing or incomplete → deduct.\n"
    "- If the answer is mostly correct but has minor issues → deduct at least 1.\n"
    "- Compare CAREFULLY against the answer key when provided. "
    "Missing any point from the answer key = deduction.\n"
    "- You are grading UNIVERSITY students, not high school. Be rigorous.\n\n"
    "Respond ONLY with this exact JSON — no markdown, no extra fields:\n"
    "{\n"
    '  "id": "<student ID or NOT FOUND>",\n'
    '  "category_scores": {\n'
    '    "Criterion Name": {"score": <0 to max_score>, "reason": "<what was right/wrong>"},\n'
    '    ...\n'
    '  }\n'
    "}\n\n"
    "CRITICAL RULES:\n"
    "- Each score must be between 0 and its max_score. Never exceed max_score.\n"
    "- reason is REQUIRED for every criterion. Never leave it empty.\n"
    "- Do NOT include 'name', 'marks', 'deductions', or 'feedback' fields.\n"
    "- Do NOT write (-N) amounts. Just give the score and reason.\n"
    "- Do NOT calculate totals. Only provide per-criterion scores.\n"
    "- MOST submissions have flaws. Giving full marks to every criterion is almost never correct."
)


def grade_submission(
    rubric: str,
    submission_text: str,
    filename: str,
    answer_key: str = None,
    cancel_event: threading.Event = None,
    preferred_name: str = "",
    preferred_id: str = "",
) -> dict:
    """
    Grade a single student submission with retry logic.
    If answer_key is provided, the LLM compares the submission to it.
    If preferred_name/preferred_id are provided, they override LLM extraction.
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
        logger.info("Answer key present — length %d chars", len(answer_key))
        prompt = (
            "Compare this student submission to the provided answer key "
            "and grade using the rubric.\n\n"
            f"Grading Rubric:\n{rubric}\n\n"
            f"{allowed_scores_note}\n\n"
            f"Answer Key / Model Solution:\n{answer_key}\n\n"
            f"Submission Filename: {filename}\n\n"
            f"Submission Content:\n{submission_text}"
        )
    else:
        prompt = (
            "Grade this student submission using the rubric only.\n\n"
            f"Grading Rubric:\n{rubric}\n\n"
            f"{allowed_scores_note}\n\n"
            f"Submission Filename: {filename}\n\n"
            f"Submission Content:\n{submission_text}"
        )

    # Guard: if submission is empty (e.g. scanned PDF with no text layer),
    # return a clean error instead of sending empty content to the grader.
    if not submission_text or len(submission_text.strip()) < 50:
        return {
            "name":            preferred_name or filename,
            "id":              preferred_id or filename,
            "marks":           "Error",
            "category_scores": {},
            "deductions":      "[No readable text found — likely a scanned image. Enable EXTRACT_IMAGES=True or convert to searchable PDF.]",
        }

    raw = retry_api_call(
        _call_llm,
        SYSTEM_PROMPT,
        prompt,
        cancel_event=cancel_event,
        max_tokens=GRADING_MAX_OUTPUT_TOKENS,
    )
    return _parse_json(
        raw,
        filename,
        rubric=rubric,
        preferred_name=preferred_name,
        preferred_id=preferred_id,
    )


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
        
        # Throttle API calls to prevent rate limits.
        # With MAX_CONCURRENT_GRADES=1, 30s sleep ≈ 2 requests/min.
        # Large submissions use 60s to allow TPM quota recovery.
        base_sleep = 30.0
        if len(sub["content"]) > 5000:
            base_sleep = 60.0
            logger.info("Large submission detected (>5k chars). Sleeping 60s for quota recovery.")
        
        time.sleep(base_sleep)
        
        identity_meta = sub.get("identity_meta", {})
        preferred_name = (
            identity_meta.get("name")
            or sub.get("lms_meta", {}).get("student_name", "")
        )
        preferred_id = identity_meta.get("id", "")
        result = grade_submission(
            rubric,
            sub["content"],
            sub["filename"],
            answer_key=answer_key,
            cancel_event=cancel_event,
            preferred_name=preferred_name,
            preferred_id=preferred_id,
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
                    "name":            sub.get("identity_meta", {}).get("name") or sub.get("lms_meta", {}).get("student_name") or sub["filename"],
                    "id":              sub.get("identity_meta", {}).get("id") or "N/A",
                    "marks":           "Error",
                    "category_scores": {},
                    "deductions":      f"Grading failed: {exc}",
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