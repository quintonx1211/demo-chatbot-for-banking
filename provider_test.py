"""Contract test for the provider adapter layer. Run: python provider_test.py

The three real adapters cannot be exercised here without their SDKs and paid
API keys. What *can* be verified - and is what actually breaks in a refactor -
is that the layer above them behaves correctly for every shape an adapter can
return: normal text, a safety refusal, a transport error, empty output.

So this substitutes a stub adapter and asserts the router's reaction. It also
checks each real adapter still satisfies the structural contract.
"""

from __future__ import annotations

import os

from app import llm
from app.llm.base import LLMRequest, LLMResult
from app.router import Router

REQUIRED_ATTRS = ("NAME", "PACKAGE", "ENV_KEYS", "available", "model_name", "complete")

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        failures.append(label)


class StubProvider:
    """Stands in for a real adapter. `behaviour` picks the response shape."""

    NAME = "stub"
    PACKAGE = "stub"
    ENV_KEYS = ("STUB_API_KEY",)

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.calls: list[LLMRequest] = []

    def available(self) -> bool:
        return True

    def model_name(self) -> str:
        return "stub-model-1"

    def complete(self, request: LLMRequest, effort: str) -> LLMResult:
        self.calls.append(request)
        if self.behaviour == "refusal":
            return LLMResult(text="", generated=False, provider=self.NAME,
                             model=self.model_name(), error="model_refusal")
        if self.behaviour == "transport_error":
            return LLMResult(text="", generated=False, provider=self.NAME,
                             model=self.model_name(),
                             error="APIConnectionError: connection reset")
        if self.behaviour == "empty":
            return LLMResult(text="   ", generated=True, provider=self.NAME,
                             model=self.model_name())
        if self.behaviour == "ungrounded":
            # Fluent, plausible, and sharing almost no vocabulary with the
            # corpus - which is exactly what the grounding check is for.
            return LLMResult(
                text=("Bộ phận chăm sóc đặc biệt của chúng tôi miễn toàn bộ phí "
                      "cho khách hàng bạch kim và hoàn tất lệnh chuyển trong "
                      "chín phút trên toàn cầu."),
                generated=True, provider=self.NAME, model=self.model_name(),
                input_tokens=900, output_tokens=40)
        # Drawn from the Vietnamese corpus so it passes the grounding check.
        # An English sentence here shares no content words with the retrieved
        # passages, scores ~0 on grounding, and escalates - the gate working
        # correctly, but it made the adapter contract test look broken.
        return LLMResult(
            text=("Chuyển tiền quốc tế thu phí 350.000 VND ở chiều đi và thời "
                  "gian ghi có 1-5 ngày làm việc tuỳ quốc gia thụ hưởng. Thời "
                  "điểm chốt giao dịch trong ngày là 16:00."),
            generated=True, provider=self.NAME, model=self.model_name(),
            input_tokens=900, output_tokens=42)


def with_stub(behaviour: str):
    """Install a stub as the active provider for one scenario."""
    stub = StubProvider(behaviour)
    llm.PROVIDERS["stub"] = stub
    os.environ["LLM_PROVIDER"] = "stub"
    os.environ["STUB_API_KEY"] = "x"
    return stub


QUESTION = "chuyển tiền quốc tế phí bao nhiêu?"


def main() -> int:
    # --- structural contract on the real adapters ------------------------
    print("\nReal adapters satisfy the contract")
    for name, provider in list(llm.PROVIDERS.items()):
        missing = [a for a in REQUIRED_ATTRS if not hasattr(provider, a)]
        check(not missing, f"{name}: exposes {', '.join(REQUIRED_ATTRS)}"
                           + (f" (missing {missing})" if missing else ""))
        check(provider.available() is False or provider.available() is True,
              f"{name}: available() returns a bool")

    # --- dispatch --------------------------------------------------------
    print("\nAdapter returning normal text")
    stub = with_stub("ok")
    router = Router()
    session = router.sessions.create()
    result = router.handle_turn(session, QUESTION)
    check(result.route == "rag", f"routed to rag (got {result.route})")
    check(result.generated, "marked as generated")
    check(len(stub.calls) == 1, "adapter called exactly once")
    check("Knowledge base passages:" in stub.calls[0].user,
          "adapter received the retrieved passages")
    check(stub.calls[0].system.startswith("You are the customer service"),
          "adapter received the answering system prompt")
    check(result.grounding is not None and result.grounding > 0.5,
          f"grounding computed ({result.grounding})")

    print("\nAdapter reporting a safety refusal")
    with_stub("refusal")
    router = Router()
    session = router.sessions.create()
    result = router.handle_turn(session, QUESTION)
    check(result.route == "escalation_offered", f"offered a handoff (got {result.route})")
    check("declined" in (result.escalation_reason or ""),
          f"reason names the refusal: {result.escalation_reason!r}")

    print("\nAdapter hitting a transport error")
    with_stub("transport_error")
    router = Router()
    session = router.sessions.create()
    result = router.handle_turn(session, QUESTION)
    check(result.route == "rag", f"served extractive fallback (got {result.route})")
    check(not result.generated, "not marked as generated")
    # Assert the fallback is verbatim corpus text, not that it contains any
    # particular words. The earlier version hardcoded a phrase from the passage
    # it expected to win, so adding a document to the corpus broke it - the
    # check was really testing retrieval ranking, which is eval_retrieval.py's
    # job and is measured there against a labelled set.
    corpus = " ".join(p.text for p in router.kb.passages)
    # The extractive fallback is "<preamble>\n\n<verbatim passage excerpt>".
    excerpt = result.text.split("\n\n", 1)[-1].strip()[:80]
    check(bool(excerpt) and excerpt in corpus,
          "fallback text is verbatim from the knowledge base")

    print("\nAdapter returning blank output")
    with_stub("empty")
    router = Router()
    session = router.sessions.create()
    result = router.handle_turn(session, QUESTION)
    check(result.text.strip() != "", "fell back rather than returning blank")
    check(not result.generated, "not marked as generated")

    print("\nAdapter returning ungrounded text")
    with_stub("ungrounded")
    router = Router()
    session = router.sessions.create()
    result = router.handle_turn(session, QUESTION)
    # A failed grounding check now *offers* a handoff rather than forcing one -
    # the customer decides whether their time is better spent in a queue.
    check(result.route == "escalation_offered", f"offered a handoff (got {result.route})")
    check("grounding" in (result.escalation_reason or "").lower(),
          f"reason names the grounding gate: {result.escalation_reason!r}")
    accepted = router.handle_turn(session, "có")
    check(accepted.route == "escalation" and session.escalated,
          f"accepting the offer escalates (got {accepted.route})")

    print("\nSummarisation goes through the same adapter")
    stub = with_stub("ok")
    router = Router()
    session = router.sessions.create()
    router.handle_turn(session, "cho tôi gặp nhân viên")
    check(session.escalated, "session escalated")
    check(any("handover brief" in c.user.lower() or "transcript" in c.user.lower()
              for c in stub.calls),
          "adapter received the summarisation request")

    print(f"\n{'-' * 60}")
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("All adapter-contract checks passed.")
    return 0


if __name__ == "__main__":
    for key in ("LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT"):
        os.environ.pop(key, None)
    raise SystemExit(main())
