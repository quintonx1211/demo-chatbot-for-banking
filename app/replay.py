"""Seed the dashboard with realistic traffic.

A dashboard computed from a three-turn demo session shows nothing, and a
dashboard populated with invented numbers is worse than none - the first
question in the room is "is this real?", and it has to be answerable with yes.

So this replays scripted *customer messages* through the real router. Nothing
about the outcome is scripted: every routing decision, retrieval, guardrail
block and escalation is produced by the same code path a live customer hits.
The conversations are fabricated; the metrics over them are not.

Deliberately mixed across the client's 3 target scenarios (automated
cross-selling, customer service on stated interests, card operations) plus
FAQ/comparison and regulated topics that get blocked and questions the corpus
does not cover, so escalations appear. A seed of nothing but successes would
make the dashboard a lie of a different kind.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import cards, db
from .router import Router

# Credentials are looked up from the fixture by given name, never written in.
# Hard-coded codes went stale the moment the customer set was regenerated, and
# a replay whose verification silently fails produces a dashboard full of
# escalations that say nothing about the assistant.
_FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "customers_seed.json")
    .read_text(encoding="utf-8"))["customers"]
_BY_NAME = {c["given_name"]: c for c in _FIXTURE}


def cred(given_name: str) -> str:
    """The two verification codes for the customer with this given name."""
    customer = _BY_NAME[given_name]
    return f"{customer['phone_last4']} {customer['national_id_last4']}"


AN = cred("An")        # MASS, stated_interests already on file ("mua sắm online")
BICH = cred("Bich")    # MASS, no stated_interests - triggers the slot-fill question
CHAU = cred("Chau")    # MASS_AFFLUENT, stated_interests on file
DUNG = cred("Dung")    # MASS_AFFLUENT, no stated_interests
HA = cred("Ha")        # AFFLUENT, stated_interests on file ("du lịch")
HIEU = cred("Hieu")    # AFFLUENT, no stated_interests
LAN = cred("Lan")      # PRIVATE, stated_interests on file ("golf", "du lịch nước ngoài")
LONG = cred("Long")    # PRIVATE, no stated_interests

# Each entry is one conversation: a list of customer messages in order.
CONVERSATIONS: list[list[str]] = [
    # --- FAQ: routine card questions (deflect to RAG) ---
    ["Phí thường niên thẻ Classic là bao nhiêu?"],
    ["Thẻ Platinum có ưu đãi chào mừng gì?"],
    ["Điều kiện miễn phí năm thứ 2 của thẻ Signature là gì?"],
    ["Thẻ Infinite có ưu đãi golf không?"],

    # --- multi-turn, still deflected ---
    ["Xin chào", "Phí thường niên thẻ Platinum là bao nhiêu?", "Cảm ơn, vậy thôi ạ"],
    ["Chào bạn", "Thẻ Signature có hoàn tiền du lịch không?", "tạm biệt"],

    # --- product comparison ---
    ["So sánh thẻ Classic và thẻ Platinum"],
    ["Khác nhau giữa thẻ Signature và thẻ Infinite là gì?"],

    # --- Scenario 2: customer states an interest, agent explains card fit ---
    ["Tôi hay mua sắm online, có ưu đãi gì không?", AN],
    ["Tôi thích đi du lịch nước ngoài", HA],
    ["Tôi hay chơi golf, thẻ có ưu đãi gì cho golf không?", LAN],
    ["Có ưu đãi nào phù hợp với sở thích của tôi không?", BICH],  # no stated_interests -> slot-fill
    ["Có ưu đãi nào phù hợp với sở thích của tôi không?", DUNG, "ăn uống"],

    # --- Scenario 3: reward inquiry, card close, limit adjustment ---
    ["Thẻ của tôi có ưu đãi gì?", HIEU],
    ["Tôi muốn đóng thẻ", LONG, "có"],
    ["Tôi muốn tăng hạn mức thẻ", CHAU, "200 triệu"],
    ["Tôi muốn đóng thẻ", AN, "không"],  # confirmation declined

    # --- customer changes their mind mid-verification (the escape path) ---
    ["Tôi muốn đóng thẻ", "thôi bỏ đi, phí thường niên thẻ Classic là bao nhiêu?"],

    # --- regulated topics: blocked before any model runs ---
    ["Tôi có nên đầu tư tiết kiệm vào cổ phiếu công nghệ không?"],
    ["Bitcoin bây giờ có đáng đầu tư không?"],
    ["Làm sao để giảm tiền thuế phải nộp?"],

    # --- outside the corpus: escalate rather than guess ---
    ["Ngân hàng có bảo hiểm cây trồng cho vườn nho không?"],
    ["Tôi mua tiền điện tử trên app được không?"],
    ["Ngân hàng có dịch vụ két sắt an toàn không?"],

    # --- the same gap, asked several ways ---
    # Real traffic repeats itself, and that repetition is the signal the topic
    # clustering exists to surface.
    ["Ngân hàng có sản phẩm bảo hiểm nhân thọ không?"],
    ["Có gói bảo hiểm nhân thọ nào không?"],
    ["Tôi muốn mua bảo hiểm nhân thọ, ngân hàng có bán không?"],

    # --- human handover: explicit requests ---
    ["Tôi muốn gặp nhân viên"],
    ["Cho tôi gặp người thật về một vấn đề khác"],

    # --- handoff offered, then accepted / declined ---
    ["Ngân hàng có dịch vụ tư vấn thuế doanh nghiệp không?", "có"],
    ["Tôi mua tiền điện tử trên app được không?", "không, cảm ơn", "Phí thường niên thẻ Signature là bao nhiêu?"],
    ["@agent tôi có vấn đề về hoá đơn"],

    # --- verification failure, then handoff ---
    ["Thẻ của tôi có ưu đãi gì?", "1111 2222", "3333 4444", "5555 6666"],
]


def seed(router: Router) -> dict:
    """Replay every conversation through `router`. Returns a short summary.

    Resets the database first, so repeated seeding starts from the same
    known state.
    """
    db.reset()
    cards.reset()
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
