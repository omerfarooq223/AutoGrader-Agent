"""
Centralized configuration — reads from environment / .env file.
"""

import os
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - fallback for minimal environments
    def load_dotenv(*_args, **_kwargs):
        return False

# Load .env from project root using robust parser.
load_dotenv()


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
