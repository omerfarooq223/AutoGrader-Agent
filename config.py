"""
Centralized configuration — reads from environment / .env file.
"""

import os
from pathlib import Path

# Load .env file if present (no external dependency needed)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            os.environ[key] = value


# ── API ─────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
MODEL: str = os.environ.get("MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# ── Configuration Toggles ───────────────────────────────────────
EXTRACT_IMAGES: bool = os.environ.get("EXTRACT_IMAGES", "False").lower() == "true"

# ── Grading ─────────────────────────────────────────────────────
MAX_CONCURRENT_GRADES: int = int(os.environ.get("MAX_CONCURRENT_GRADES", "1"))
MAX_RETRIES: int = int(os.environ.get("MAX_RETRIES", "3"))
TOTAL_MARKS: int = int(os.environ.get("TOTAL_MARKS", "100"))
PASS_THRESHOLD: int = int(os.environ.get("PASS_THRESHOLD", "50"))
GRADING_MAX_OUTPUT_TOKENS: int = int(os.environ.get("GRADING_MAX_OUTPUT_TOKENS", "768"))
# Note: Set to 0 to disable truncation and send full content to LLM
MAX_SUBMISSION_CHARS: int = int(os.environ.get("MAX_SUBMISSION_CHARS", "0"))
MAX_ANSWER_KEY_CHARS: int = int(os.environ.get("MAX_ANSWER_KEY_CHARS", "0"))
MAX_RUBRIC_CHARS: int = int(os.environ.get("MAX_RUBRIC_CHARS", "0"))

# ── Plagiarism ──────────────────────────────────────────────────
SIMILARITY_THRESHOLD: float = float(os.environ.get("SIMILARITY_THRESHOLD", "0.65"))

# ── Output ──────────────────────────────────────────────────────
OUTPUT_FILENAME: str = os.environ.get("OUTPUT_FILENAME", "grading_report.xlsx")
CACHE_FILENAME: str = os.environ.get("CACHE_FILENAME", ".grading_cache.json")
