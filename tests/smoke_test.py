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
        ["xin chào", "số dư của tôi còn bao nhiêu?", cred("travel_offer")],
        "deterministic", True,
    ),
    (
        "Card block - two-step confirmation, writes to the record",
        ["tôi làm mất thẻ ghi nợ", cred("multi_card"), "6591", "có"],
        "deterministic", True,
    ),
    (
        "Freeze then unfreeze - the reversible path",
        ["tạm khoá thẻ giúp tôi", cred("loan_in_review"), "có",
         "mở khoá thẻ giúp tôi", "có"],
        "deterministic", True,
    ),
    (
        "Loan status lookup",
        ["hồ sơ vay của tôi đến đâu rồi?", cred("loan_in_review")],
        "deterministic", True,
    ),
    (
        "RAG-grounded answer from the knowledge base",
        ["chuyển tiền quốc tế phí bao nhiêu?"],
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
        "Handoff declined - assistant carries on",
        ["ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?", "không, cảm ơn",
         "chi nhánh mở cửa mấy giờ?"],
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
