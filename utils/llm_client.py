"""
LLM Client — unified interface for Groq (primary) and Gemini (fallback).

Groq is used first. If a rate limit (429) or daily token exhaustion error
is encountered, the client automatically falls back to Gemini Flash,
which has a much larger free-tier limit (1.5M tokens/day).

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
def _call_gemini(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError(
            "google-generativeai is not installed. "
            "Run: pip install google-generativeai"
        )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY not set. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=system_prompt,
    )
    response = model.generate_content(
        user_prompt,
        generation_config={"max_output_tokens": max_tokens, "temperature": 0.2},
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
    groq_error_msg = None

    # Try Groq first
    try:
        return _call_groq(system_prompt, user_prompt, groq_model, max_tokens)
    except Exception as groq_err:
        err_str = str(groq_err).lower()
        is_rate_limit = (
            "429" in str(groq_err)
            or "rate_limit" in err_str
            or "tokens per day" in err_str
        )
        groq_error_msg = str(groq_err)  # save before exception var is deleted

        if is_rate_limit:
            logger.warning(
                "Groq rate limit hit — falling back to Gemini Flash. Error: %s",
                groq_error_msg,
            )
        else:
            raise

    # Gemini fallback
    try:
        result = _call_gemini(system_prompt, user_prompt, max_tokens)
        logger.info("Gemini fallback succeeded.")
        return result
    except EnvironmentError as gemini_key_err:
        raise RuntimeError(
            "Groq rate limit reached and GEMINI_API_KEY is not set. "
            "Either wait for Groq quota to reset, or add GEMINI_API_KEY to your .env file. "
            "Get a free Gemini key at https://aistudio.google.com/app/apikey"
        ) from gemini_key_err
    except Exception as gemini_err:
        raise RuntimeError(
            f"Both Groq and Gemini failed.\n"
            f"Groq error: {groq_error_msg}\n"
            f"Gemini error: {gemini_err}"
        ) from gemini_err