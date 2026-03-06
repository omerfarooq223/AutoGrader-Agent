"""
Cache utility — saves intermediate grading results to disk so runs
can be resumed after a crash without re-grading already-finished files.
"""

import json
import logging
from pathlib import Path

from config import CACHE_FILENAME

logger = logging.getLogger(__name__)


def _cache_path(base_dir: str) -> Path:
    return Path(base_dir) / CACHE_FILENAME


def load_cache(base_dir: str) -> dict[str, dict]:
    """Load cached results. Returns {filename: result_dict}."""
    path = _cache_path(base_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info("Loaded cache with %d entries from %s", len(data), path)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read cache (%s), starting fresh.", exc)
        return {}


def save_cache(base_dir: str, results: dict[str, dict]) -> None:
    """Persist results dict to cache file."""
    path = _cache_path(base_dir)
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_cache(base_dir: str) -> None:
    """Remove cache file."""
    path = _cache_path(base_dir)
    if path.exists():
        path.unlink()
        logger.info("Cache cleared: %s", path)
