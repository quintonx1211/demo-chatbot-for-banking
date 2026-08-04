"""Seed the dashboard with realistic traffic.

A dashboard computed from a three-turn demo session shows nothing, and a
dashboard populated with invented numbers is worse than none — the first
question in the room is "is this real?", and it has to be answerable with yes.

So this replays scripted *customer messages* through the real router. Nothing
about the outcome is scripted: every routing decision, retrieval, guardrail
block and escalation is produced by the same code path a live customer hits.
The conversations are fabricated; the metrics over them are not.

Deliberately mixed: routine questions that deflect, account actions that need
verification, regulated topics that get blocked, and questions the corpus does
not cover so escalations appear. A seed of nothing but successes would make the
dashboard a lie of a different kind.
"""

from __future__ import annotations

from .router import Router

# Each entry is one conversation: a list of customer messages in order.
CONVERSATIONS: list[list[str]] = [
    # --- routine knowledge questions (deflect) ---
    ["What are your branch opening hours?"],
    ["How do I reset my online banking password?"],
    ["How much is the overdraft fee?"],
    ["What documents do I need to open an account?"],
    ["How long does a mortgage application take?"],
    ["Can I pay my loan off early without a penalty?"],
    ["How long do I have to dispute a transaction?"],
    ["What is the mobile cheque deposit limit?"],
    ["What does an international wire cost?"],
    ["How do I avoid the monthly maintenance fee?"],
    ["I got a suspicious email claiming to be from you"],
    ["When does contactless ask for a PIN?"],
    ["How long until a replacement card arrives?"],
    ["What rate would I get on a car loan?"],
    ["How long do I have to fight a chargeback?"],
    ["How far in advance do I send the payroll file?"],

    # --- multi-turn, still deflected ---
    ["Hello", "What are your fees for international transfers?", "Thanks, that's all"],
    ["Hi there", "How do I dispute a charge I didn't make?", "goodbye"],
    ["What happens if I go overdrawn?", "And is there a cap on that?"],

    # --- account actions: verification then a scripted flow ---
    ["Check my balance", "4471"],
    ["Show me my recent transactions", "9032"],
    ["What's the status of my loan application?", "4471"],
    ["What's the status of my mortgage?", "9032"],
    ["Can you tell me who am I?", "4471"],
    ["I lost my debit card", "9032", "yes"],

    # --- customer changes their mind mid-flow (the escape path) ---
    ["I lost my debit card", "4471", "actually never mind, what are your branch hours?"],

    # --- regulated topics: blocked before any model runs ---
    ["Should I invest my savings in tech stocks?"],
    ["Is bitcoin a good investment right now?"],
    ["How can I reduce my tax bill?"],

    # --- outside the corpus: escalate rather than guess ---
    ["Do you offer crop insurance for vineyards?"],
    ["Can I buy cryptocurrency in the app?"],
    ["Do you provide safe deposit boxes?"],

    # --- the same gap, asked several ways ---
    # Real traffic repeats itself, and that repetition is the signal the topic
    # clustering exists to surface: five people asking about travel insurance
    # is a document to write, not five escalations to staff.
    ["Do you offer travel insurance with the card?"],
    ["Is travel insurance included on my credit card?"],
    ["Does my card come with travel insurance cover?"],
    ["What travel insurance do I get with the card?"],
    ["Can I add travel insurance to my card?"],

    ["Can I use my card on the metro in Tokyo?"],
    ["Will my card work on public transport abroad?"],
    ["Does contactless work on foreign transit systems?"],

    # --- campaign scenarios (the client's three) ---
    ["How do I activate my new card?", "3390"],
    ["My new card arrived, how do I start using it?", "3390"],
    ["Do you have any offers for me?", "4471"],
    ["Am I eligible for an upgrade?", "4471"],
    ["Any promotions for me?", "9032"],
    ["My card hasn't been used in ages, is it still ok?", "9032"],

    # --- handoff offered, then accepted / declined ---
    ["Do you offer safe deposit boxes?", "yes"],
    ["Can I buy cryptocurrency in the app?", "no thanks", "What are your branch hours?"],
    ["@agent I have a billing problem"],

    # --- explicit handoff requests ---
    ["I want to speak to a human"],
    ["Let me talk to a real person about a duplicate charge"],

    # --- verification failure, then handoff ---
    ["Check my balance", "1111", "2222", "3333"],
]


def seed(router: Router) -> dict:
    """Replay every conversation through `router`. Returns a short summary."""
    before = len(router.sessions._sessions)

    for messages in CONVERSATIONS:
        session = router.sessions.create()
        for message in messages:
            router.handle_turn(session, message)

    sessions = list(router.sessions._sessions.values())
    return {
        "conversations_added": len(CONVERSATIONS),
        "sessions_total": len(sessions),
        "sessions_before": before,
        "turns_added": sum(len(c) for c in CONVERSATIONS),
    }
