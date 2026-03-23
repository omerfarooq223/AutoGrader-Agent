"""
Rubric Agent — reads the assignment brief and generates a grading rubric
via the Groq API (LLaMA 3.3 70B), then pauses for user approval.

Features:
  - Structured rubric with explicit category mark allocations
  - Marks scaling to match total marks found in the brief
  - Retry with exponential backoff
  - Save / load rubric to disk for reuse across runs
"""

import json
import logging
import os
import re
from pathlib import Path

from groq import Groq

from config import MODEL
from utils.retry import retry_api_call
from utils.llm_client import call_llm

logger = logging.getLogger(__name__)

RUBRIC_CACHE = ".rubric_cache.json"
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "rubrics"


# ── Helpers ─────────────────────────────────────────────────────

def _extract_total_marks(brief_text: str, fallback: int) -> int:
    """Extract total marks from assignment brief using regex, fallback to config.TOTAL_MARKS."""
    patterns = [
        r"total\s*marks?\s*[:=]?\s*(\d+)",
        r"total\s*[:=]?\s*(\d+)",
        r"out\s+of\s*(\d+)",
        r"/\s*(\d+)",
        r"marks\s*[:=]?\s*(\d+)",
        r"points\s*[:=]?\s*(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, brief_text, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1))
                if 1 <= val <= 1000:   # sanity check
                    return val
            except (ValueError, IndexError):
                continue
    return fallback


def _scale_rubric_criteria(rubric: dict, total_marks: int) -> dict:
    """Scale rubric criteria so their max_score values sum exactly to total_marks."""
    criteria = rubric.get("criteria", [])
    if not criteria:
        return rubric
    current_total = sum(c.get("max_score", 0) for c in criteria)
    if current_total == 0 or current_total == total_marks:
        return rubric
    # Scale each criterion proportionally
    scaled = []
    for c in criteria:
        new_score = round(c["max_score"] / current_total * total_marks)
        scaled.append({**c, "max_score": new_score})
    # Fix rounding drift by adjusting the largest criterion
    diff = total_marks - sum(c["max_score"] for c in scaled)
    if diff != 0:
        idx = max(range(len(scaled)), key=lambda i: scaled[i]["max_score"])
        scaled[idx]["max_score"] += diff
    return {"criteria": scaled}


def _load_templates() -> list[dict]:
    """Load all rubric templates from the rubrics/ directory."""
    templates = []
    if not _TEMPLATES_DIR.is_dir():
        return templates
    for path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            templates.append(data)
        except (json.JSONDecodeError, OSError):
            logger.debug("Skipping invalid template: %s", path)
    return templates


def _match_template(brief_text: str) -> dict | None:
    """Return the best-matching rubric template for the brief, or None."""
    brief_lower = brief_text.lower()
    best, best_count = None, 0
    for tmpl in _load_templates():
        keywords = tmpl.get("match_keywords", [])
        hits = sum(1 for kw in keywords if kw.lower() in brief_lower)
        if hits > best_count:
            best, best_count = tmpl, hits
    return best if best_count >= 2 else None


def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def _parse_rubric_json(raw: str) -> dict:
    """Extract and validate the structured rubric JSON from LLM output."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    rubric = json.loads(text)
    if "criteria" not in rubric or not isinstance(rubric["criteria"], list):
        raise ValueError("Rubric JSON missing 'criteria' list.")
    for item in rubric["criteria"]:
        if not all(k in item for k in ("name", "max_score", "description")):
            raise ValueError(f"Criterion missing required keys: {item}")
        if not isinstance(item["max_score"], (int, float)) or item["max_score"] <= 0:
            raise ValueError(f"Invalid max_score for '{item['name']}': {item['max_score']}")
    return rubric


# ── Core functions ───────────────────────────────────────────────

def generate_rubric(brief_text: str) -> str:
    """
    Send the assignment brief to the LLM and return a structured rubric as
    a JSON string. Automatically scales marks to match total found in brief.

    Schema: {"criteria": [{"name": "...", "max_score": N, "description": "..."}, ...]}
    """
    import config

    client   = _get_client()
    template = _match_template(brief_text)

    if template:
        template_json = json.dumps(
            {"criteria": template["criteria"]}, indent=2, ensure_ascii=False
        )
        logger.info("Using rubric template: %s", template.get("template_name", "unknown"))

        system_prompt = (
            "You are an expert academic grading assistant. "
            "You are given a rubric template and an assignment brief.\n\n"
            "Your job is to:\n"
            "- Keep the exact criterion names from the template.\n"
            "- Adjust the max_score weights if needed so they fit this specific "
            "assignment (they must still sum to the total marks).\n"
            "- Write a detailed description for EACH criterion explaining criteria "
            "for full marks, partial marks, and zero marks.\n\n"
            "Respond ONLY with valid JSON — no markdown, no extra text:\n"
            '{"criteria": [{"name": "...", "max_score": <number>, "description": "..."}, ...]}'
        )
        user_content = (
            f"Rubric Template:\n{template_json}\n\n"
            f"Assignment Brief:\n\n{brief_text}"
        )
    else:
        logger.info("No matching rubric template; generating from scratch.")

        system_prompt = (
            "You are an expert academic grading assistant. "
            "Given an assignment brief, produce a detailed grading rubric.\n\n"
            "Requirements:\n"
            "- Divide into clear categories relevant to the assignment type.\n"
            "- Assign specific mark allocations. max_score values must sum to the "
            "total marks stated in the brief (or 100 if not stated).\n"
            "- For EACH category write a description covering full, partial, "
            "and zero marks.\n\n"
            "Respond ONLY with valid JSON — no markdown, no extra text:\n"
            '{"criteria": [{"name": "Category Name", "max_score": <number>, "description": "..."}, ...]}'
        )
        user_content = f"Assignment Brief:\n\n{brief_text}"

    def _call(sp=system_prompt, up=user_content):
        raw = call_llm(sp, up)
        _parse_rubric_json(raw)   # validate; invalid JSON triggers retry
        return raw

    raw         = retry_api_call(_call)
    rubric_dict = _parse_rubric_json(raw)

    # Scale marks to match total found in brief
    total_marks = _extract_total_marks(
        brief_text, getattr(config, "TOTAL_MARKS", 100)
    )
    rubric_dict = _scale_rubric_criteria(rubric_dict, total_marks)

    return json.dumps(rubric_dict, indent=2, ensure_ascii=False)


def refine_rubric_descriptions(rubric_json: str) -> str:
    """
    Send a rubric to the LLM to improve descriptions only.
    Criterion names and mark allocations are never changed.
    """
    client = _get_client()

    system_prompt = (
        "You are an expert academic grading assistant. "
        "Improve the clarity and specificity of the rubric descriptions below. "
        "Do NOT change any criterion names or max_score values. "
        "Only rewrite the description of each criterion to be more specific, "
        "measurable, and useful for grading. "
        "Return the same JSON structure with only descriptions improved. "
        "No markdown, no extra text."
    )

    def _call(sp=system_prompt, up=rubric_json):
        raw = call_llm(sp, up)
        _parse_rubric_json(raw)
        return raw

    return retry_api_call(_call)


def save_rubric(rubric: str, base_dir: str = ".") -> None:
    """Persist the approved rubric to disk for reuse."""
    path = Path(base_dir) / RUBRIC_CACHE
    path.write_text(
        json.dumps({"rubric": rubric}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Rubric saved to %s", path)


def load_rubric(base_dir: str = ".") -> str | None:
    """Load a previously saved rubric, or return None."""
    path = Path(base_dir) / RUBRIC_CACHE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("rubric")
    except (json.JSONDecodeError, OSError):
        return None


def approve_rubric(rubric: str) -> str:
    """
    CLI approval flow — display rubric and wait for user input.
    Returns the (possibly edited) approved rubric, or "" to signal regeneration.
    """
    print("\n" + "=" * 60)
    print("PROPOSED GRADING RUBRIC")
    print("=" * 60)
    print(rubric)
    print("=" * 60)

    while True:
        choice = input("\n[A]pprove  /  [E]dit  /  [R]egenerate  → ").strip().upper()
        if choice == "A":
            logger.info("Rubric approved by user.")
            return rubric
        elif choice == "E":
            print("Paste your edited rubric (end with a blank line):")
            lines: list[str] = []
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
            return "\n".join(lines)
        elif choice == "R":
            return ""
        else:
            print("Please enter A, E, or R.")