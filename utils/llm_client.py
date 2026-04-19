"""
LLM Client — unified interface for Gemini (primary) and Groq (fallback).

Gemini is used first. If Gemini fails (quota/rate/availability/model access),
the client automatically falls back to Groq.

Usage:
    from utils.llm_client import call_llm

    response_text = call_llm(system_prompt, user_prompt)
"""

import logging
import os

logger = logging.getLogger(__name__)

# ── Groq ────────────────────────────────────────────────────────
def _call_groq(system_prompt: str, user_prompt: str, model: str, max_tokens: int) -> str:
    from groq import Groq
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY not set.")
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        timeout=30.0,  # Hard 30s timeout — prevents infinite hangs on large submissions
    )
    return response.choices[0].message.content.strip()


# ── Gemini ───────────────────────────────────────────────────────
def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    model_name: str | None = None,
) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError(
            "google-genai is not installed. "
            "Run: pip install google-genai"
        )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY not set. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )
    # Remove GOOGLE_API_KEY so the SDK doesn't silently override our key
    os.environ.pop("GOOGLE_API_KEY", None)
    client = genai.Client(api_key=api_key)
    import config
    selected_model = model_name or config.GEMINI_MODEL
    logger.info("Calling Gemini with model: %s", selected_model)
    
    response = client.models.generate_content(
        model=selected_model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=0.0,
        ),
    )
    return response.text.strip()



# ── Circuit breaker state ──────────────────────────────────────────
_provider_failure_counts = {"groq": 0, "gemini": 0}
_provider_circuit_open = {"groq": False, "gemini": False}
CIRCUIT_BREAKER_THRESHOLD = 3  # Open circuit after 3 consecutive failures


def _check_quota_exhausted(error_msg: str) -> bool:
    """Check if error indicates permanent quota exhaustion."""
    error_lower = error_msg.lower()
    return (
        "limit: 0" in error_msg or
        "quota" in error_lower or
        "daily" in error_lower or
        "permanently" in error_lower
    )


def _should_skip_provider(provider: str) -> bool:
    """Check if provider should be skipped due to circuit breaker."""
    if _provider_circuit_open.get(provider, False):
        logger.warning("Circuit breaker OPEN for %s — skipping provider.", provider.upper())
        return True
    return False


def _record_failure(provider: str):
    """Record a provider failure and potentially open circuit breaker."""
    _provider_failure_counts[provider] = _provider_failure_counts.get(provider, 0) + 1
    if _provider_failure_counts[provider] >= CIRCUIT_BREAKER_THRESHOLD:
        _provider_circuit_open[provider] = True
        logger.error(
            "Circuit breaker OPENED for %s after %d failures.",
            provider.upper(), CIRCUIT_BREAKER_THRESHOLD
        )


def _record_success(provider: str):
    """Reset failure counter on success."""
    _provider_failure_counts[provider] = 0


# ── Unified entry point ──────────────────────────────────────────
def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    max_tokens: int = 2048,
) -> str:
    import config
    import re
    import time
    
    groq_model = model or config.MODEL
    gemini_models = [config.GEMINI_MODEL, "gemini-2.0-flash", "gemini-flash-latest"]
    
    last_error = None
    
    # Try Groq first — it's proven reliable and fast
    if not _should_skip_provider("groq"):
        try:
            if not os.environ.get("GROQ_API_KEY"):
                logger.warning("GROQ_API_KEY missing — skipping Groq.")
            else:
                logger.info("Attempting Groq with model: %s", groq_model)
                result = _call_groq(system_prompt, user_prompt, groq_model, max_tokens)
                _record_success("groq")
                logger.info("✓ Groq succeeded")
                return result
        except Exception as groq_err:
            err_msg = str(groq_err)
            last_error = groq_err
            
            # Check for quota exhaustion
            if _check_quota_exhausted(err_msg):
                _record_failure("groq")
                logger.error("Groq quota exhausted: %s", err_msg[:200])
            # Check for rate limit with retry backoff
            elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg.upper():
                logger.warning("Groq rate limit hit: %s", err_msg[:200])
                match = re.search(r'retry in ([\d\.]+)s', err_msg)
                if match:
                    wait = float(match.group(1)) + 1.0
                    logger.warning("Groq requested wait: %.1fs. Retrying once...", wait)
                    time.sleep(wait)
                    try:
                        result = _call_groq(system_prompt, user_prompt, groq_model, max_tokens)
                        _record_success("groq")
                        logger.info("✓ Groq succeeded after retry")
                        return result
                    except Exception as retry_err:
                        logger.warning("Groq retry failed: %s", retry_err)
                        last_error = retry_err
            else:
                logger.warning("Groq failed: %s", err_msg[:200])
                _record_failure("groq")
    
    # Gemini fallbacks — only try if not circuit-broken
    if not _should_skip_provider("gemini"):
        for gem_model in gemini_models:
            try:
                logger.info("Attempting Gemini fallback with model: %s", gem_model)
                result = _call_gemini(system_prompt, user_prompt, max_tokens, model_name=gem_model)
                _record_success("gemini")
                logger.info("✓ Gemini succeeded with model: %s", gem_model)
                return result
            except Exception as gemini_err:
                err_msg = str(gemini_err)
                logger.warning("Gemini %s failed: %s", gem_model, err_msg[:200])
                last_error = gemini_err
                
                if _check_quota_exhausted(err_msg):
                    _record_failure("gemini")
                    logger.error("Gemini quota exhausted, skipping remaining models.")
                    break  # Skip other Gemini models
    
    # All providers failed
    error_summary = f"All LLM providers failed (Groq circuit: {_provider_circuit_open.get('groq')}, Gemini circuit: {_provider_circuit_open.get('gemini')})"
    logger.error(error_summary)
    if last_error:
        raise RuntimeError(f"{error_summary}\nLast error: {last_error}")
    else:
        raise RuntimeError(error_summary)