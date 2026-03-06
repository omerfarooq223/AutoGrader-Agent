"""
Rubric Agent — reads the assignment brief and generates a grading rubric
via the Groq API (LLaMA 3.3 70B), then pauses for user approval.

Features:
  - Structured rubric with explicit category mark allocations
  - Retry with exponential backoff
  - Save / load rubric to disk for reuse across runs
"""

import json
import logging
import os
from pathlib import Path

from groq import Groq

from config import MODEL
from utils.retry import retry_api_call

logger = logging.getLogger(__name__)

RUBRIC_CACHE = ".rubric_cache.json"
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "rubrics"


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

    # Validate structure
    if "criteria" not in rubric or not isinstance(rubric["criteria"], list):
        raise ValueError("Rubric JSON missing 'criteria' list.")
    for item in rubric["criteria"]:
        if not all(k in item for k in ("name", "max_score", "description")):
            raise ValueError(f"Criterion missing required keys: {item}")
        if not isinstance(item["max_score"], (int, float)) or item["max_score"] <= 0:
            raise ValueError(f"Invalid max_score for '{item['name']}': {item['max_score']}")

    return rubric


def generate_rubric(brief_text: str) -> str:
    """
    Send the assignment brief to the LLM and return a structured rubric as
    a JSON string with the schema:
    {"criteria": [{"name": "...", "max_score": N, "description": "..."}, ...]}

    If a matching template is found in rubrics/, the LLM fills in descriptions
    and adjusts weights rather than generating from scratch.
    """
    client = _get_client()
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
            "- Adjust the max_score weights if needed so they make sense for this "
            "specific assignment (they must still sum to the total marks).\n"
            "- Write a detailed description for EACH criterion that explains "
            "criteria for full marks, partial marks, and zero marks, "
            "tailored to this assignment brief.\n\n"
            "Respond ONLY with valid JSON in this EXACT format (no markdown, no extra text):\n"
            '{\n'
            '  "criteria": [\n'
            '    {"name": "...", "max_score": <number>, "description": "..."},\n'
            '    ...\n'
            '  ]\n'
            '}'
        )

        user_content = (
            f"Rubric Template:\n{template_json}\n\n"
            f"Assignment Brief:\n\n{brief_text}"
        )
    else:
        logger.info("No matching rubric template found; generating from scratch.")

        system_prompt = (
            "You are an expert academic grading assistant. "
            "Given an assignment brief, produce a detailed grading rubric.\n\n"
            "Requirements:\n"
            "- Divide into clear categories (e.g., Correctness, Code Quality, Documentation).\n"
            "- Assign specific mark allocations for each category. The max_score values "
            "across all criteria should sum to the total marks for the assignment.\n"
            "- For EACH category, write a description that explains criteria for "
            "full marks, partial marks, and zero marks.\n"
            "- Include a category for presentation / formatting if relevant.\n\n"
            "Respond ONLY with valid JSON in this EXACT format (no markdown, no extra text):\n"
            '{\n'
            '  "criteria": [\n'
            '    {"name": "Category Name", "max_score": <number>, "description": "..."},\n'
            '    ...\n'
            '  ]\n'
            '}'
        )

        user_content = f"Assignment Brief:\n\n{brief_text}"

    def _call():
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        raw = response.choices[0].message.content.strip()
        # Validate before returning — invalid JSON will trigger retry
        _parse_rubric_json(raw)
        return raw

    raw = retry_api_call(_call)
    # Return pretty-printed JSON for readability in UI / approval flow
    rubric_dict = _parse_rubric_json(raw)
    return json.dumps(rubric_dict, indent=2, ensure_ascii=False)


def save_rubric(rubric: str, base_dir: str = ".") -> None:
    """Persist the approved rubric to disk for reuse."""
    path = Path(base_dir) / RUBRIC_CACHE
    path.write_text(json.dumps({"rubric": rubric}, ensure_ascii=False, indent=2), encoding="utf-8")
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
    Display the rubric and wait for user approval.
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
            print("Paste your edited rubric below (end with an empty line):")
            lines: list[str] = []
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
            rubric = "\n".join(lines)
            logger.info("User edited the rubric.")
            return rubric
        elif choice == "R":
            return ""  # Signal to caller to regenerate
        else:
            print("Please enter A, E, or R.")
