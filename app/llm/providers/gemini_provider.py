"""Google Gemini adapter (google-genai SDK).

Two structural differences from the other two providers:

  * The system prompt is `system_instruction` on the request config, not a
    message in the conversation.
  * A safety block can happen at two different points - the prompt can be
    blocked before generation (`prompt_feedback.block_reason`) or the candidate
    can be blocked after (`finish_reason == SAFETY`). Both must be mapped onto
    the single `model_refusal` signal the router escalates on.

Context caching exists but requires explicitly creating a cache object with a
TTL, unlike the inline `cache_control` marker on the Claude path. It is not
wired up here - at this prompt size it would cost more to manage than it saves.
"""

from __future__ import annotations

import os

from ..base import LLMRequest, LLMResult, classify_provider_error

NAME = "gemini"
# Flash rather than Pro: Google's free tier covers the Flash line only, and the
# demo's job is rewriting a retrieved passage into two sentences - not work that
# needs a Pro-tier model. Override with LLM_MODEL if you have paid access.
DEFAULT_MODEL = "gemini-2.5-flash"
PACKAGE = "google-genai"
ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# Gemini expresses reasoning depth as a token budget, not a named level.
# -1 lets the model decide; 0 disables thinking entirely.
_THINKING_BUDGET = {"low": 0, "medium": 1024, "high": -1}


def available() -> bool:
    if not any(os.environ.get(key) for key in ENV_KEYS):
        return False
    try:
        from google import genai  # noqa: F401
    except ImportError:
        return False
    return True


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def complete(request: LLMRequest, effort: str) -> LLMResult:
    from google import genai
    from google.genai import types

    api_key = next((os.environ[k] for k in ENV_KEYS if os.environ.get(k)), None)
    client = genai.Client(api_key=api_key)
    model = model_name()

    config = types.GenerateContentConfig(
        system_instruction=request.system,
        max_output_tokens=request.max_tokens,
        thinking_config=types.ThinkingConfig(
            thinking_budget=_THINKING_BUDGET.get(effort, 0)
        ),
        **({"temperature": request.temperature}
           if request.temperature is not None else {}),
    )

    if request.messages:
        # Gemini uses "model" for the assistant role, and its own Content/Part types.
        contents = [
            types.Content(
                role="user" if m["role"] == "user" else "model",
                parts=[types.Part(text=m["content"])],
            )
            for m in request.messages
        ]
    else:
        contents = request.user

    try:
        response = client.models.generate_content(
            model=model, contents=contents, config=config
        )
    except Exception as exc:
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error=classify_provider_error(exc))

    # Block point 1: the prompt never reached the model.
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error="model_refusal")

    # Block point 2: generation started, then the candidate was withheld.
    candidates = getattr(response, "candidates", None) or []
    finish_reason = str(getattr(candidates[0], "finish_reason", "")) if candidates else ""
    if "SAFETY" in finish_reason or "RECITATION" in finish_reason:
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error="model_refusal")

    text = (getattr(response, "text", None) or "").strip()
    usage = getattr(response, "usage_metadata", None)
    return LLMResult(
        text=text,
        generated=bool(text),
        provider=NAME,
        model=model,
        input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
        error="truncated" if "MAX_TOKENS" in finish_reason else None,
    )
