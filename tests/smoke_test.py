"""End-to-end smoke test for the routing layers. Run: python tests/smoke_test.py

Exercises every branch of the router without needing the HTTP server or an API
key - the generative layer degrades to extractive mode when no key is present.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The Windows console defaults to cp1252, which cannot encode Vietnamese. Now
# that the assistant answers in Vietnamese, printing a reply raised
# UnicodeEncodeError and took the whole test run down - a test suite that dies
# on its own output reports nothing about the code it was meant to check.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app import cards, db
from app.replay import cred
from app.router import Router

# Scenario tuples are (title, turns, expected_route, must_verify).
#
# `must_verify` exists because of a bug this file was hiding, back when it
# tested account actions. A failed identity check is *also* "deterministic" -
# it returns "those details don't match our records" from a scripted flow -
# so a route assertion alone cannot tell a working personalised flow apart
# from one whose verification silently regressed. The session state is the
# only thing that actually proves the identity check ran and passed.
#
# Codes come from the fixture via `cred()`, so regenerating customers no
# longer silently breaks the tests. Covers the client's 3 target scenarios:
# #1 Automated Cross-Selling (proactive pitch after verification), #2
# Customer Service (customer states an interest, agent explains card fit),
# #3 Card Operations (close card, adjust limit, reward inquiry).
SCENARIOS: list[tuple[str, list[str], str, bool]] = [
    (
        "Scenario 1: proactive cross-sell pitch after verification (Mass segment target)",
        ["thẻ của tôi có ưu đãi gì?", cred("An")],
        "deterministic", True,
    ),
    (
        "Scenario 2: customer states an interest, agent explains why the card fits",
        ["tôi thích đi du lịch nước ngoài", cred("Ha")],
        "deterministic", True,
    ),
    (
        "Scenario 2: no stated interest on file - one-question slot-fill",
        ["có ưu đãi nào phù hợp với sở thích của tôi không?", cred("Bich"), "mua sắm online"],
        "deterministic", True,
    ),
    (
        "Scenario 3: reward inquiry - lists the card's reward lines",
        ["thẻ của tôi có ưu đãi gì?", cred("Hieu")],
        "deterministic", True,
    ),
    (
        "Scenario 3: close card - confirmed then closed",
        ["tôi muốn đóng thẻ", cred("Long"), "có"],
        "deterministic", True,
    ),
    (
        "Scenario 3: limit adjustment request - logged, never auto-approved",
        ["tôi muốn tăng hạn mức thẻ", cred("Chau"), "200 triệu"],
        "deterministic", True,
    ),
    (
        "Product comparison - public, no identity needed",
        ["so sánh thẻ Classic và thẻ Platinum"],
        "deterministic", False,
    ),
    (
        "RAG-grounded FAQ answer from the knowledge base",
        ["phí thường niên thẻ Classic là bao nhiêu?"],
        "rag", False,
    ),
    (
        "Guardrail - restricted topic refused before any model call",
        ["tôi có nên đầu tư tiết kiệm vào cổ phiếu công nghệ không?"],
        "guardrail", False,
    ),
    (
        "Handoff offered - no supporting passage, customer decides",
        ["ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?"],
        "escalation_offered", False,
    ),
    (
        "Handoff accepted - the offer is taken up",
        ["ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?", "có"],
        "escalation", False,
    ),
    (
        "Handoff declined - assistant carries on and answers the next question",
        ["ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?", "không, cảm ơn",
         "phí thường niên thẻ Classic là bao nhiêu?"],
        "rag", False,
    ),
    (
        "@agent goes straight to a human",
        ["@agent tôi bị trừ tiền hai lần"],
        "escalation", False,
    ),
    (
        "Escalation - customer asks for a human",
        ["cho tôi gặp nhân viên"],
        "escalation", False,
    ),
]


def main() -> int:
    # Card Operations scenarios mutate data/db/cards.csv on disk (close,
    # limit-adjust request) - reset first so a rerun starts from the same
    # known state instead of tripping on a card an earlier run already closed.
    db.reset()
    cards.reset()

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
