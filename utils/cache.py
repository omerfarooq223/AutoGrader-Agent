"""
Cache utility — saves intermediate grading results to disk so runs
can be resumed after a crash without re-grading already-finished files.

Production hardening:
  - Atomic writes (write temp → rename) to prevent corrupt cache on crash
  - Duplicate filename detection with content-hash disambiguation
  - Explicit error logging on save failure instead of silent data loss
"""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from config import CACHE_FILENAME

logger = logging.getLogger(__name__)

# Bump this when the result format changes to auto-discard stale caches.
# v2: deterministic Python scoring (scores/deductions computed in Python, not LLM)
CACHE_VERSION = 2


def _cache_path(base_dir: str) -> Path:
    return Path(base_dir) / CACHE_FILENAME


def _make_safe_key(filename: str, content: str = None) -> str:
    """
    Build a cache key that survives duplicate filenames.
    If content is provided, appends a short hash so two students
    with identical filenames get separate cache entries.
    """
    if not content:
        return filename
    short_hash = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:8]
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    return f"{stem}_{short_hash}{suffix}"


def load_cache(base_dir: str) -> dict[str, dict]:
    """Load cached results. Returns {cache_key: result_dict}."""
    path = _cache_path(base_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Check version — discard stale caches from older scoring logic
        if data.get("__cache_version__") != CACHE_VERSION:
            logger.warning(
                "Cache version mismatch (got %s, want %s) — discarding stale cache.",
                data.get("__cache_version__"), CACHE_VERSION,
            )
            path.unlink()
            return {}
        entries = {k: v for k, v in data.items() if k != "__cache_version__"}
        logger.info("Loaded cache with %d entries from %s", len(entries), path)
        return entries
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read cache (%s), starting fresh.", exc)
        return {}


def save_cache(base_dir: str, results: dict[str, dict]) -> None:
    """
    Persist results dict to cache file using an atomic write.
    Writes to a temp file first, then renames — so a crash during
    writing never leaves a corrupted cache file behind.
    """
    path = _cache_path(base_dir)
    try:
        dir_path = path.parent
        # Embed version marker so stale caches are auto-discarded on load
        versioned = {"__cache_version__": CACHE_VERSION, **results}
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dir_path,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(versioned, tmp, indent=2, ensure_ascii=False)
            tmp_path = tmp.name
        # Atomic on POSIX (Linux/Mac). On Windows this is near-atomic.
        os.replace(tmp_path, path)
    except OSError as exc:
        logger.error(
            "CACHE WRITE FAILED — grading continues but progress "
            "will not survive a crash. Reason: %s", exc
        )


def clear_cache(base_dir: str) -> None:
    """Remove cache file."""
    path = _cache_path(base_dir)
    if path.exists():
        path.unlink()
        logger.info("Cache cleared: %s", path)
