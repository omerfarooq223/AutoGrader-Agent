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
                # Default exponential backoff
                wait = float(2 ** (attempt + 1))
                
                # Check for 429 / Rate Limit / Quota and try to parse the requested wait time
                exc_str = str(exc)
                if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str.upper() or "RATE_LIMIT" in exc_str.upper():
                    import re
                    # Look for "Please retry in X.Xs" or similar (Gemini style)
                    matches = re.search(r'retry in ([\d\.]+)s', exc_str)
                    if matches:
                        wait = float(matches.group(1)) + 1.0  # Add 1s buffer
                        logger.info("Rate limit hit. API requested a wait of %.1fs", wait)
                    else:
                        # Harder backoff for 429 if no specific time is found
                        wait = float(max(wait, 30.0))
                
                logger.warning(
                    "API call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1, max_retries, exc, wait,
                )
                # Interruptible sleep
                start_sleep = time.time()
                while time.time() - start_sleep < wait:
                    if cancel_event and cancel_event.is_set():
                        logger.info("API call cancelled during backoff sleep.")
                        raise InterruptedError("Cancelled by user")
                    time.sleep(0.1)
            else:
                logger.error("API call failed after %d attempts: %s", max_retries + 1, exc)
    raise last_exc
