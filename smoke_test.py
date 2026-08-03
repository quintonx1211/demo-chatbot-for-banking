"""End-to-end smoke test for the routing layers. Run: python smoke_test.py

Exercises every branch of the router without needing the HTTP server or an API
key - the generative layer degrades to extractive mode when no key is present.
"""

from app.router import Router

SCENARIOS: list[tuple[str, list[str], str]] = [
    (
        "Deterministic flow with identity verification",
        ["hi", "what's my balance?", "4471"],
        "deterministic",
    ),
    (
        "Card block - two-step confirmation, writes to the record",
        ["I lost my debit card", "9032", "yes"],
        "deterministic",
    ),
    (
        "Loan status lookup",
        ["any update on my loan application?", "9032"],
        "deterministic",
    ),
    (
        "RAG-grounded answer from the knowledge base",
        ["what do you charge for an international wire?"],
        "rag",
    ),
    (
        "Guardrail - restricted topic refused before any model call",
        ["should I invest my savings in tech stocks?"],
        "guardrail",
    ),
    (
        "Escalation - no supporting passage in the knowledge base",
        ["do you offer crop insurance for vineyards in Portugal?"],
        "escalation",
    ),
    (
        "Escalation - customer asks for a human",
        ["let me talk to a real person"],
        "escalation",
    ),
]


def main() -> int:
    router = Router()
    failures = 0

    for title, turns, expected_route in SCENARIOS:
        session = router.sessions.create()
        print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")

        result = None
        for turn in turns:
            result = router.handle_turn(session, turn)
            print(f"\n  > {turn}")
            print(f"    [{result.route} | {result.intent} @ "
                  f"{result.confidence:.2f} | {result.latency_ms}ms]")
            for line in result.text.splitlines():
                print(f"    {line}")
            if result.sources:
                print("    sources: " +
                      ", ".join(f"{s['citation']} ({s['score']})" for s in result.sources))

        status = "PASS" if result.route == expected_route else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"\n  {status} - expected route '{expected_route}', got '{result.route}'")

        if session.escalated:
            print(f"\n  Handover brief:\n"
                  + "\n".join(f"    {l}" for l in
                              (session.escalation_summary or "").splitlines()))

    print(f"\n{'=' * 74}")
    print(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
