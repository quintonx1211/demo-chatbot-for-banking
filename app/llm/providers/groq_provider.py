"""Groq adapter.

Groq serves open-weight models behind an OpenAI-compatible Chat Completions
API, so this uses the `openai` SDK pointed at Groq's host - no separate
package. It is a provider in its own right rather than a base-URL override on
the OpenAI adapter, so an operator can pick "groq" in the console, paste a
GROQ_API_KEY, and get a working default model without also knowing the host.

Why it is worth having: Groq's free tier is comfortable for a demo - at the
time of writing, 30 requests/min and 1,000/day on Llama 3.3 70B - which makes
it the cheapest way to exercise the full RAG path end to end.

Two behaviours differ from OpenAI itself and are handled below:

  * No `refusal` field. OpenAI reports a safety decline in a dedicated field
    that maps onto the `model_refusal` escalation; Groq returns the decline as
    ordinary assistant text, which is indistinguishable from an answer at this
    layer. The grounding gate becomes the only thing that catches it.
  * Reasoning models (gpt-oss, qwen3) spend part of the token budget "thinking"
    before any answer text, and default to medium effort. LLM_EFFORT is mapped
    to `reasoning_effort` for those models so replies stay fast and the reasoning
    can't eat the whole budget and truncate the answer; models that don't accept
    the param never receive it.
"""

from __future__ import annotations

import os
import re
import time

from ..base import LLMRequest, LLMResult, classify_provider_error


def _parse_duration(value: str) -> float | None:
    """Đổi chuỗi thời lượng của Groq ('577ms', '22s', '1m26.4s', '5') -> giây."""
    v = str(value).strip()
    try:
        if v.endswith("ms"):
            return float(v[:-2]) / 1000
        m = re.fullmatch(r"(?:(\d+)m)?([\d.]+)s", v)
        if m:
            return int(m.group(1) or 0) * 60 + float(m.group(2))
        return float(v.rstrip("s"))
    except (ValueError, TypeError):
        return None


def _rate_limit_wait(exc: Exception) -> float | None:
    """Số giây nên đợi nếu đây là lỗi 429 (vượt trần TPM/RPM), hoặc None nếu không
    phải rate limit. Đọc retry-after / reset từ header, cap để không treo request."""
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status != 429 and "ratelimit" not in type(exc).__name__.lower():
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for key in (
        "retry-after",
        "x-ratelimit-reset-tokens",
        "x-ratelimit-reset-requests",
    ):
        secs = _parse_duration(headers.get(key)) if headers.get(key) else None
        if secs is not None:
            return min(max(secs, 1.0), 12.0)
    return 5.0


NAME = "groq"
PACKAGE = "openai"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
ENV_KEYS = ("GROQ_API_KEY",)

BASE_URL = "https://api.groq.com/openai/v1"


def available() -> bool:
    if not any(os.environ.get(key) for key in ENV_KEYS):
        return False
    try:
        import openai
    except ImportError:
        return False
    return True


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def base_url() -> str:
    """Fixed - reported by /api/health so the endpoint is never a surprise."""
    return BASE_URL


def complete(request: LLMRequest, effort: str) -> LLMResult:
    from openai import OpenAI

    model = model_name()
    client = OpenAI(
        api_key=next((os.environ[k] for k in ENV_KEYS if os.environ.get(k)), None),
        base_url=BASE_URL,
    )

    if request.messages:
        msgs = [{"role": "system", "content": request.system}] + request.messages
    else:
        msgs = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]

    extra: dict = {}
    if request.temperature is not None:
        extra["temperature"] = request.temperature
    if any(tag in model.lower() for tag in ("gpt-oss", "qwen3")):
        extra["reasoning_effort"] = (
            effort if effort in ("low", "medium", "high") else "low"
        )

    response = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                max_completion_tokens=request.max_tokens,
                messages=msgs,
                **extra,
            )
            break
        except Exception as exc:
            wait = _rate_limit_wait(exc)
            if wait is not None and attempt < 2:
                time.sleep(wait)
                continue
            return LLMResult(
                text="",
                generated=False,
                provider=NAME,
                model=model,
                error=classify_provider_error(exc),
            )

    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = response.usage
    return LLMResult(
        text=text,
        generated=bool(text),
        provider=NAME,
        model=getattr(response, "model", model),
        input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        error="truncated" if choice.finish_reason == "length" else None,
    )
