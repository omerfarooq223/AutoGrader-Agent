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

from utils.retry import retry_api_call
from utils.llm_client import call_llm

logger = logging.getLogger(__name__)

RUBRIC_CACHE = os.environ.get("RUBRIC_CACHE_FILENAME", ".rubric_cache.json")
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
        # type: ignore (Pyre thinks scaled is a strict TypedDict and won't allow reassignment of max_score here)
        scaled[idx]["max_score"] = int(scaled[idx]["max_score"]) + diff
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
    # Threshold: must match at least 2 keywords AND 40% of the template's keywords
    if best and best_count >= 2:
        total_keywords = len(best.get('match_keywords', []))
        if total_keywords == 0 or best_count / total_keywords >= 0.4:
            return best
    return None



def _parse_rubric_json(raw: str) -> dict:
    """Extract and validate the structured rubric JSON from LLM output."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    # Try direct parse first
    try:
        rubric = json.loads(text)
    except json.JSONDecodeError:
        # Extract the first top-level JSON object via brace matching
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in LLM response.")
        depth, end = 0, start
        for i, ch in enumerate(text[start:], start):  # type: ignore
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        rubric = json.loads(text[start:end + 1])      # type: ignore
    if "criteria" not in rubric or not isinstance(rubric["criteria"], list):
        raise ValueError("Rubric JSON missing 'criteria' list.")
    for item in rubric["criteria"]:
        if not all(k in item for k in ("name", "max_score", "description")):
            raise ValueError(f"Criterion missing required keys: {item}")
        
        # Robustly handle max_score (cast strings to float, handle None)
        try:
            val = item["max_score"]
            if val is None:
                item["max_score"] = 1.0
            else:
                item["max_score"] = float(val)
        except (ValueError, TypeError):
            item["max_score"] = 1.0
            
        if item["max_score"] <= 0:
            item["max_score"] = 1.0
    return rubric


# ── Core functions ───────────────────────────────────────────────

def generate_rubric(brief_text: str) -> str:
    """
    Send the assignment brief to the LLM and return a structured rubric as
    a JSON string. Automatically scales marks to match total found in brief.

    Schema: {"criteria": [{"name": "...", "max_score": N, "description": "..."}, ...]}
    """
    import config

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

    result = json.dumps(rubric_dict, indent=2, ensure_ascii=False)
    return _strip_marks_from_bands(result)


def _strip_marks_from_bands(rubric_json_str: str) -> str:
    """
    Replace [N Marks]: / [N Mark]: with semantic labels in description fields.
    Prevents LLM from confusing band scores with deduction amounts during grading.
    Keeps the highest score as [Full], middle as [Partial], lowest as [Minimal].
    """
    import re as _re
    try:
        obj = json.loads(rubric_json_str)
        for criterion in obj.get("criteria", []):
            desc = criterion.get("description", "")
            if not desc:
                continue
            # Find all [N Marks]: patterns and their positions
            matches = list(_re.finditer(r"\[\d+\s*Marks?\]:", desc, _re.IGNORECASE))
            if len(matches) >= 3:
                # Sort by the number to assign Full/Partial/Minimal correctly
                scored = sorted(
                    matches,
                    key=lambda m: int(_re.search(r"\d+", m.group()).group()),
                    reverse=True,
                )
                labels = ["[Full]:", "[Partial]:", "[Minimal]:"]
                for match, label in zip(scored, labels):
                    desc = desc.replace(match.group(), label, 1)
                criterion["description"] = desc
            elif len(matches) == 2:
                scored = sorted(
                    matches,
                    key=lambda m: int(_re.search(r"\d+", m.group()).group()),
                    reverse=True,
                )
                for match, label in zip(scored, ["[Full]:", "[Minimal]:"]):
                    desc = desc.replace(match.group(), label, 1)
                criterion["description"] = desc
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception:
        return rubric_json_str  # If anything fails, return unchanged


def format_rubric_to_json(rubric_text: str) -> str:
    """
    Send a rubric (in any format: JSON, CSV, plain text) to the LLM to
    extract it into a standardized JSON rubric.
    Never rewrite or change the names, allocations, or descriptions.
    """
    system_prompt = (
        "You are an expert academic grading assistant. "
        "You will receive a grading rubric in any format (JSON, CSV table, "
        "plain text, markdown, etc.).\n\n"
        "Your job is to strictly parse it into a standardized JSON format.\n"
        "1. Identify every criterion, its maximum possible score, and its description.\n"
        "2. Keep the EXACT criterion names and wording unchanged.\n"
        "3. If the rubric has multiple columns for different performance levels (e.g., 'Full Marks', 'Partial', 'Minimal' or '5 pts', '3 pts', '1 pt'):\n"
        "   - Use the HIGHEST score found for that criterion as the 'max_score'.\n"
        "   - MERGE all the level descriptions into the single 'description' field.\n"
        "   - Use labels to keep it organized, e.g., '[5 Marks]: ... [3 Marks]: ... [1 Mark]: ...'\n"
        "4. If a criterion is missing a mark allocation entirely, default it to 1.\n"
        "5. IMPORTANT: Never include header rows as criteria. If the first row contains words like 'Criterion', 'Category', 'Name', or 'Criteria' as the criterion name, skip it entirely — it is a table header, not a real criterion.\n\n"
        "Respond ONLY with valid JSON — no markdown, no extra text:\n"
        '{"criteria": [{"name": "...", "max_score": <number>, '
        '"description": "..."}, ...]}'
    )

    def _call(sp=system_prompt, up=rubric_text):
        raw = call_llm(sp, up)
        _parse_rubric_json(raw)
        return raw

    result = retry_api_call(_call)
    return _strip_marks_from_bands(result)


def save_rubric(rubric: str, base_dir: str = ".") -> None:
    """Persist the approved rubric to disk for reuse (atomic write)."""
    import tempfile
    path = Path(base_dir) / RUBRIC_CACHE
    payload = json.dumps({"rubric": rubric}, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        delete=False, suffix=".tmp"
    ) as tmp:
        tmp.write(payload)
        tmp_path = tmp.name
    os.replace(tmp_path, path)
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
    return ""