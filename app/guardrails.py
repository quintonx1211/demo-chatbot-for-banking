"""Compliance guardrails applied around the generative layer.

Three jobs:
  1. Refuse regulated topics outright (investment / tax / legal advice) before
     a model ever sees them.
  2. Redact PII from anything that gets written to the audit log.
  3. Check that a generated answer is actually supported by the retrieved
     passages, so an ungrounded answer escalates instead of shipping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .textmodel import tokenize

# Topics the assistant must never answer generatively, however confident the
# model is. Each maps to the reason recorded in the audit trail.
RESTRICTED_TOPICS: list[tuple[str, re.Pattern, str]] = [
    (
        "investment_advice",
        re.compile(
            r"\b(should i (invest|buy|sell)|which (stock|fund|crypto)|"
            r"invest(ment)? advice|portfolio allocation|is .* a good investment|"
            r"will .* (go up|crash)|best (stock|fund|coin)s?)\b",
            re.IGNORECASE,
        ),
        "Investment recommendations require a licensed advisor.",
    ),
    (
        "tax_advice",
        re.compile(
            r"\b(tax (advice|deduction|loophole)|how (do|can) i (avoid|reduce) tax|"
            r"write.?off .* on my taxes)\b",
            re.IGNORECASE,
        ),
        "Tax guidance requires a qualified tax professional.",
    ),
    (
        "legal_advice",
        re.compile(r"\b(should i sue|legal advice|is it legal (for|to)|take you to court)\b",
                   re.IGNORECASE),
        "Legal questions must be handled by the bank's legal team.",
    ),
]

RESTRICTED_RESPONSE = (
    "I'm not able to give investment, tax, or legal advice - that has to come "
    "from a licensed professional. I can connect you with a banking specialist "
    "who can talk through the products we offer, or answer any question about "
    "your accounts, cards, or applications."
)

# Redaction patterns, applied in order. Card numbers first so their digits are
# not partially eaten by the generic long-number rule.
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # The trailing separator is matched but not consumed, so "4111 1111 1111
    # 1111 twice" masks to "[CARD_REDACTED] twice" rather than running the
    # marker into the next word. Cosmetic, but this string is what gets shown
    # when demonstrating that masking works.
    (re.compile(r"\b(?:\d[ -]?){13,19}(?<![ -])\b"), "[CARD_REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b(?:\+?\d{1,2}[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"), "[PHONE_REDACTED]"),
]


@dataclass
class GuardrailVerdict:
    allowed: bool
    topic: str | None = None
    reason: str | None = None


def check_input(text: str) -> GuardrailVerdict:
    for topic, pattern, reason in RESTRICTED_TOPICS:
        if pattern.search(text):
            return GuardrailVerdict(allowed=False, topic=topic, reason=reason)
    return GuardrailVerdict(allowed=True)


def redact(text: str) -> str:
    """Strip PII before a message is persisted to the audit log."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def grounding_score(answer: str, context: str) -> float:
    """Fraction of the answer's content words that appear in the retrieved context.

    A cheap, deterministic proxy for "did the model stay inside the sources".
    It cannot catch a subtly wrong number, but it reliably catches an answer
    invented wholesale - which is the failure mode that matters for compliance.
    """
    answer_terms = set(tokenize(answer))
    if not answer_terms:
        return 0.0
    context_terms = set(tokenize(context))
    return len(answer_terms & context_terms) / len(answer_terms)


# Below this the answer is not considered supported by the knowledge base.
# Read from the policy file so the strictness trade-off is a setting, not a
# recompile - see app/policy.py and config.json.
from . import policy as _policy


def min_grounding() -> float:
    return _policy.current.min_grounding


MIN_GROUNDING = _policy.current.min_grounding
