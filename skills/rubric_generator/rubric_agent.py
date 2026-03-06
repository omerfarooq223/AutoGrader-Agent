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


def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def generate_rubric(brief_text: str) -> str:
    """
    Send the assignment brief to the LLM and return a proposed rubric string.
    """
    client = _get_client()

    system_prompt = (
        "You are an expert academic grading assistant. "
        "Given an assignment brief, produce a detailed grading rubric.\n\n"
        "Requirements for the rubric:\n"
        "- Divide into clear categories (e.g., Correctness, Code Quality, Documentation).\n"
        "- Assign specific mark allocations that sum to the total marks.\n"
        "- For EACH category, describe criteria for full marks, partial marks, and zero marks.\n"
        "- Include a category for presentation / formatting if relevant.\n"
        "- Format as a numbered Markdown list with sub-bullets.\n"
        "- End with a summary table: | Category | Max Marks |"
    )

    def _call():
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Assignment Brief:\n\n{brief_text}"},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return response.choices[0].message.content.strip()

    return retry_api_call(_call)


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
