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
        temperature=0.2,
        max_tokens=max_tokens,
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
    print(f"DEBUG: Calling Gemini with model='{selected_model}'")
    logger.info("Calling Gemini with model: %s", selected_model)
    
    response = client.models.generate_content(
        model=selected_model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=0.2,
        ),
    )
    return response.text.strip()



# ── Unified entry point ──────────────────────────────────────────
def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    max_tokens: int = 2048,
) -> str:
    import config
    groq_model = model or config.MODEL
    gemini_models = [config.GEMINI_MODEL, "gemini-2.0-flash", "gemini-flash-latest"]
    
    # Try Groq first — it's proven reliable and fast in this region
    try:
        if not os.environ.get("GROQ_API_KEY"):
            logger.warning("GROQ_API_KEY missing from environment — skipping Groq.")
            raise EnvironmentError("GROQ_API_KEY not set.")
            
        logger.warning("DEBUG: Trying Groq with model '%s'…", groq_model)
        result = _call_groq(system_prompt, user_prompt, groq_model, max_tokens)
        logger.info("Groq succeeded with model: %s", groq_model)
        return result
    except Exception as groq_err:
        err_msg = str(groq_err)
        logger.warning("DEBUG: Groq Failed: %s", err_msg[:200])
        if "429" in err_msg or "rate_limit" in err_msg.lower():
           import re, time
           match = re.search(r'retry in ([\d\.]+)s', err_msg)
           wait = float(match.group(1)) + 1.0 if match else 60.0
           logger.warning("Groq rate limit hit. Waiting %.1fs and retrying Groq...", wait)
           time.sleep(wait)
           return _call_groq(system_prompt, user_prompt, groq_model, max_tokens)
        else:
            logger.warning("Groq failed, trying Gemini fallbacks: %s", groq_err)
        last_error = groq_err

    # Gemini fallbacks
    for gem_model in gemini_models:
        try:
            logger.warning("DEBUG: Trying Gemini fallback with model '%s'…", gem_model)
            result = _call_gemini(system_prompt, user_prompt, max_tokens, model_name=gem_model)
            logger.info("Gemini fallback succeeded with model: %s", gem_model)
            return result
        except Exception as gemini_err:
            logger.warning("DEBUG: Gemini fallback '%s' Failed: %s", gem_model, str(gemini_err)[:200])
            logger.debug("Gemini fallback %s failed: %s", gem_model, gemini_err)
            continue

    raise RuntimeError(
        f"All LLM providers failed.\n"
        f"Groq Error: {last_error}\n"
        "Gemini models also exceeded quota or were unavailable."
    )