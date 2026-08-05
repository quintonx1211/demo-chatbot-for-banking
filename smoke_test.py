"""End-to-end smoke test for the routing layers. Run: python smoke_test.py

Exercises every branch of the router without needing the HTTP server or an API
key - the generative layer degrades to extractive mode when no key is present.
"""

from app.replay import cred
from app.router import Router

# Scenario tuples are (title, turns, expected_route, must_verify).
#
# `must_verify` exists because of a bug this file was hiding. Three scenarios
# hard-coded verification codes, and asserted only that the final route was
# "deterministic". A failed identity check is *also* deterministic - it returns
# "those details don't match our records" from a scripted flow - so when the
# customer fixture was regenerated and the codes stopped existing, the test
# went on passing while testing nothing but the failure path. A route assertion
# alone cannot tell those two apart; the session state can.
#
# Codes come from the fixture via `cred()`, so regenerating customers no longer
# silently breaks the tests.
SCENARIOS: list[tuple[str, list[str], str, bool]] = [
    (
        "Deterministic flow with identity verification",
        ["hi", "what's my balance?", cred("travel_offer")],
        "deterministic", True,
    ),
    (
        "Card block - two-step confirmation, writes to the record",
        ["I lost my debit card", cred("multi_card"), "6591", "yes"],
        "deterministic", True,
    ),
    (
        "Freeze then unfreeze - the reversible path",
        ["freeze my card", cred("loan_in_review"), "yes",
         "unblock my card", "yes"],
        "deterministic", True,
    ),
    (
        "Loan status lookup",
        ["any update on my loan application?", cred("loan_in_review")],
        "deterministic", True,
    ),
    (
        "RAG-grounded answer from the knowledge base",
        ["what do you charge for an international wire?"],
        "rag", False,
    ),
    (
        "Guardrail - restricted topic refused before any model call",
        ["should I invest my savings in tech stocks?"],
        "guardrail", False,
    ),
    (
        "Handoff offered - no supporting passage, customer decides",
        ["do you offer crop insurance for vineyards in Portugal?"],
        "escalation_offered", False,
    ),
    (
        "Handoff accepted - the offer is taken up",
        ["do you offer crop insurance for vineyards in Portugal?", "yes"],
        "escalation", False,
    ),
    (
        "Handoff declined - assistant carries on",
        ["do you offer crop insurance for vineyards in Portugal?", "no thanks",
         "what are your branch hours?"],
        "rag", False,
    ),
    (
        "@agent goes straight to a human",
        ["@agent I have a problem with a duplicate charge"],
        "escalation", False,
    ),
    (
        "Escalation - customer asks for a human",
        ["let me talk to a real person"],
        "escalation", False,
    ),
]


def main() -> int:
    router = Router()
    failures = 0

    for title, turns, expected_route, must_verify in SCENARIOS:
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

        problems = []
        if result.route != expected_route:
            problems.append(
                f"expected route '{expected_route}', got '{result.route}'")
        if must_verify and not session.verified:
            problems.append("identity was never verified - the flow ran its "
                            "failure path, which the route alone cannot detect")

        status = "FAIL" if problems else "PASS"
        failures += bool(problems)
        detail = "; ".join(problems) or f"route '{result.route}', identity verified"
        print(f"\n  {status} - {detail}")

        if session.escalated:
            print(f"\n  Handover brief:\n"
                  + "\n".join(f"    {l}" for l in
                              (session.escalation_summary or "").splitlines()))

    print(f"\n{'=' * 74}")
    print(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
