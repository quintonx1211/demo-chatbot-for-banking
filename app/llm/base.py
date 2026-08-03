"""Provider-neutral core of the generative layer.

Holds everything that does not depend on which vendor answers: the two prompts,
the request/response contract (`LLMRequest` / `LLMResult`), and the extractive
fallback used when no provider is configured.

A provider adapter only has to turn an `LLMRequest` into an `LLMResult`. Nothing
above this module - router, guardrails, session, server - knows a vendor name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..retriever import RetrievedPassage

# Sized well above the few sentences we want back. Reasoning-capable models
# spend part of this budget on thinking before emitting any answer text, so a
# tight cap truncates the answer mid-thought rather than saving anything.
ANSWER_MAX_TOKENS = 4000
SUMMARY_MAX_TOKENS = 3000

ANSWER_SYSTEM_PROMPT = """You are the customer service assistant for Regional Trust Bank.

You answer ONLY from the knowledge base passages provided in the user message. \
These passages are the bank's verified documentation and are the sole source of \
truth available to you.

Rules, in priority order:
1. Every factual claim - figures, fees, timeframes, eligibility, policy - must \
come from the passages. Never supply a number or condition that is not written \
there, even if you believe it is correct.
2. If the passages do not cover the question, or cover it only partially, say so \
plainly and offer to connect the customer with a specialist. Do not fill gaps.
3. Never give investment, tax, or legal advice, and never speculate about an \
individual customer's account, application, or eligibility - you cannot see \
account data.
4. Never ask for, repeat, or confirm a full card number, PIN, password, or \
one-time passcode.

Style: warm and direct, second person, 2-4 short sentences or a brief bullet \
list. No preamble, no restating the question. Do not cite document IDs - the \
interface renders sources separately."""

SUMMARY_SYSTEM_PROMPT = """You write handover briefs for bank contact-centre agents \
picking up a conversation escalated from the virtual assistant.

Produce exactly these four sections, in this order, using these headings:

**Customer & verification** - who they are and whether identity was verified, in one line.
**What they asked for** - the actual goal, not a turn-by-turn replay.
**What the assistant did** - actions already taken (blocks placed, data shown) and what it could not resolve.
**Recommended next step** - one concrete action for the agent.

Be factual and compressed: an agent reads this in under fifteen seconds. State \
only what the transcript supports - if something is unclear, write "not \
established". Never include card numbers, passcodes, or other credentials."""


@dataclass
class LLMRequest:
    """What every adapter receives. Deliberately the lowest common denominator:
    one system instruction, one user turn, one token ceiling."""
    system: str
    user: str
    max_tokens: int


@dataclass
class LLMResult:
    text: str
    generated: bool          # True if a model produced it, False for the offline path
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def refused(self) -> bool:
        """A provider safety system declined. The router escalates on this."""
        return self.error == "model_refusal"


def format_context(passages: list[RetrievedPassage]) -> str:
    blocks = []
    for index, item in enumerate(passages, start=1):
        blocks.append(
            f"[Passage {index} | {item.passage.doc_id} | {item.passage.title} - "
            f"{item.passage.heading}]\n{item.passage.text}"
        )
    return "\n\n".join(blocks)


def build_answer_request(
    question: str,
    passages: list[RetrievedPassage],
    history: str = "",
) -> LLMRequest:
    history_block = (
        f"Earlier in this conversation:\n{history}\n\n" if history.strip() else ""
    )
    return LLMRequest(
        system=ANSWER_SYSTEM_PROMPT,
        user=(
            f"{history_block}"
            f"Knowledge base passages:\n\n{format_context(passages)}\n\n"
            f"---\nCustomer question: {question}\n\n"
            "Answer using only the passages above."
        ),
        max_tokens=ANSWER_MAX_TOKENS,
    )


def build_summary_request(transcript: str, context_lines: list[str]) -> LLMRequest:
    metadata = "\n".join(f"- {line}" for line in context_lines)
    return LLMRequest(
        system=SUMMARY_SYSTEM_PROMPT,
        user=(
            f"Session facts:\n{metadata}\n\n"
            f"Full transcript:\n{transcript}\n\n"
            "Write the handover brief."
        ),
        max_tokens=SUMMARY_MAX_TOKENS,
    )


# -- offline fallbacks ----------------------------------------------------
# Return verified text verbatim rather than paraphrasing it. Less fluent than a
# generated answer, but it cannot hallucinate - the right trade-off for a
# fallback path in a regulated setting.

def extractive_answer(passages: list[RetrievedPassage]) -> str:
    if not passages:
        return ""
    best = passages[0].passage
    sentences = re.split(r"(?<=[.!?])\s+", best.text)
    excerpt = " ".join(sentences[:3]).strip()
    return (f"Here's what our documentation says about **{best.heading.lower()}**:\n\n"
            f"{excerpt}")


def extractive_summary(transcript: str, context_lines: list[str]) -> str:
    customer_turns = [
        line.split(":", 1)[1].strip()
        for line in transcript.splitlines()
        if line.startswith("Customer:")
    ]
    facts = "\n".join(f"- {line}" for line in context_lines)
    asked = customer_turns[0] if customer_turns else "not established"
    latest = customer_turns[-1] if customer_turns else "not established"
    return (
        "**Customer & verification**\n"
        f"{facts}\n\n"
        "**What they asked for**\n"
        f"- Opening request: {asked}\n"
        f"- Most recent message: {latest}\n\n"
        "**What the assistant did**\n"
        f"- Handled {len(customer_turns)} customer turn(s); see the transcript below.\n\n"
        "**Recommended next step**\n"
        "- Review the transcript and continue from the last customer message.\n\n"
        "_(Offline summary - configure an LLM provider for the written brief.)_"
    )
