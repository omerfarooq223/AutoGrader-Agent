"""
Retry helper with exponential backoff for API calls.
"""

import time
import logging

from config import MAX_RETRIES

logger = logging.getLogger(__name__)


def retry_api_call(func, *args, max_retries: int = MAX_RETRIES, cancel_event=None, **kwargs):
    """
    Call `func(*args, **kwargs)` with exponential backoff on failure.

    Retries on any Exception up to `max_retries` times.
    Waits 2^attempt seconds between retries (2s, 4s, 8s …).
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        if cancel_event and cancel_event.is_set():
            logger.info("API call cancelled by event before execution.")
            raise InterruptedError("Cancelled by user")
            
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
                # Interruptible sleep
                for _ in range(int(wait * 10)):
                    if cancel_event and cancel_event.is_set():
                        logger.info("API call cancelled during backoff sleep.")
                        raise InterruptedError("Cancelled by user")
                    time.sleep(0.1)
            else:
                logger.error("API call failed after %d attempts: %s", max_retries + 1, exc)
    raise last_exc
