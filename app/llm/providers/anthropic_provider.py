"""Anthropic (Claude) adapter."""

from __future__ import annotations

import os

from ..base import LLMRequest, LLMResult, classify_provider_error

NAME = "anthropic"
DEFAULT_MODEL = "claude-opus-5"
PACKAGE = "anthropic"
ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Claude takes effort directly as low | medium | high | xhigh | max.
_EFFORT = {"low": "low", "medium": "medium", "high": "high"}


def available() -> bool:
    if not any(os.environ.get(key) for key in ENV_KEYS):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def complete(request: LLMRequest, effort: str) -> LLMResult:
    import anthropic

    client = anthropic.Anthropic()
    model = model_name()

    if request.messages:
        api_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in request.messages
        ]
    else:
        api_messages = [{"role": "user", "content": request.user}]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=request.max_tokens,
            system=[{
                "type": "text",
                "text": request.system,
                # The system prompt is byte-stable across every turn, so it
                # caches and each subsequent request only pays for the context.
                "cache_control": {"type": "ephemeral"},
            }],
            output_config={"effort": _EFFORT.get(effort, "low")},
            messages=api_messages,
            # Omitted entirely when unset, so the provider default stands.
            **({"temperature": request.temperature}
               if request.temperature is not None else {}),
        )
    except Exception as exc:  # network, auth, rate limit - degrade, don't crash
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error=classify_provider_error(exc))

    # Claude signals a safety decline with a stop_reason, not an exception, and
    # returns HTTP 200 - so this must be checked before reading content.
    if response.stop_reason == "refusal":
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error="model_refusal")

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return LLMResult(
        text=text,
        generated=bool(text),
        provider=NAME,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        error="truncated" if response.stop_reason == "max_tokens" else None,
    )
