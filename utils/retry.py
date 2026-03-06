"""
Retry helper with exponential backoff for API calls.
"""

import time
import logging

from config import MAX_RETRIES

logger = logging.getLogger(__name__)


def retry_api_call(func, *args, max_retries: int = MAX_RETRIES, **kwargs):
    """
    Call `func(*args, **kwargs)` with exponential backoff on failure.

    Retries on any Exception up to `max_retries` times.
    Waits 2^attempt seconds between retries (2s, 4s, 8s …).
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    "API call failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, max_retries, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error("API call failed after %d attempts: %s", max_retries + 1, exc)
    raise last_exc
