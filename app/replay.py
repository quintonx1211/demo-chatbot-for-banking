"""Seed the dashboard with realistic traffic.

A dashboard computed from a three-turn demo session shows nothing, and a
dashboard populated with invented numbers is worse than none - the first
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

import json
from pathlib import Path

from . import db
from .router import Router

# Credentials are looked up from the fixture by scenario, never written in.
# Hard-coded codes went stale the moment the customer set was regenerated, and
# a replay whose verification silently fails produces a dashboard full of
# escalations that say nothing about the assistant.
_FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "accounts.json")
    .read_text(encoding="utf-8"))["customers"]
_BY_PROFILE = {c["profile"]: c for c in _FIXTURE}


def cred(profile: str) -> str:
    """The two verification codes for the customer playing `profile`."""
    customer = _BY_PROFILE[profile]
    return f"{customer['phone_last4']} {customer['national_id_last4']}"


TRAVEL = cred("travel_offer")
DORMANT = cred("dormant_card")
INACTIVE = cred("inactive_card")
LOAN = cred("loan_in_review")
MORTGAGE = cred("mortgage_approved")
FROZEN = cred("frozen_card")

# Each entry is one conversation: a list of customer messages in order.
CONVERSATIONS: list[list[str]] = [
    # --- routine knowledge questions (deflect) ---
    ["Chi nhánh mở cửa mấy giờ?"],
    ["Tôi quên mật khẩu ngân hàng số thì làm sao?"],
    ["Phí thấu chi là bao nhiêu?"],
    ["Mở tài khoản cần giấy tờ gì?"],
    ["Hồ sơ vay mua nhà xử lý bao lâu?"],
    ["Trả nợ trước hạn có bị phạt không?"],
    ["Tôi có bao nhiêu ngày để tra soát giao dịch?"],
    ["Hạn mức nộp séc qua ứng dụng là bao nhiêu?"],
    ["Chuyển tiền quốc tế phí bao nhiêu?"],
    ["Làm sao để không bị thu phí quản lý tài khoản?"],
    ["Tôi nhận được email lạ tự xưng là ngân hàng"],
    ["Khi nào thanh toán không tiếp xúc bắt nhập PIN?"],
    ["Bao lâu thì nhận được thẻ thay thế?"],
    ["Lãi suất vay mua ô tô là bao nhiêu?"],
    ["Thời hạn gửi yêu cầu bồi hoàn là bao lâu?"],
    ["Nộp file chi lương trước bao lâu?"],

    # --- multi-turn, still deflected ---
    ["Xin chào", "Phí chuyển tiền quốc tế thế nào?", "Cảm ơn, vậy thôi ạ"],
    ["Chào bạn", "Tôi muốn tra soát một giao dịch lạ", "tạm biệt"],
    ["Nếu tài khoản bị âm thì sao?", "Có giới hạn số lần không?"],

    # --- account actions: verification then a scripted flow ---
    ["Kiểm tra số dư", TRAVEL],
    ["Xem giao dịch gần đây", DORMANT],
    ["Hồ sơ vay của tôi đến đâu rồi?", LOAN],
    ["Hồ sơ vay mua nhà của tôi thế nào?", MORTGAGE],
    ["Tôi là ai trong hệ thống?", TRAVEL],
    ["Tôi làm mất thẻ ghi nợ", DORMANT, "có"],

    # --- freeze, then change their mind: the reversible path ---
    # Present so the card-event log shows a transition going back, not only
    # one-way blocks. The reversible case is the one customers actually hit.
    ["tạm khoá thẻ giúp tôi", TRAVEL, "có"],
    ["mở khoá thẻ giúp tôi", FROZEN, "có"],

    # --- customer changes their mind mid-flow (the escape path) ---
    ["Tôi làm mất thẻ ghi nợ", TRAVEL, "thôi bỏ đi, chi nhánh mở cửa mấy giờ?"],

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
    # clustering exists to surface: five people asking about travel insurance
    # is a document to write, not five escalations to staff.
    ["Thẻ có kèm bảo hiểm du lịch không?"],
    ["Thẻ tín dụng của tôi có bảo hiểm du lịch không?"],
    ["Thẻ của tôi có được bảo hiểm du lịch không?"],
    ["Bảo hiểm du lịch đi kèm thẻ gồm những gì?"],
    ["Tôi mua thêm bảo hiểm du lịch cho thẻ được không?"],

    ["Thẻ của tôi dùng được ở tàu điện Tokyo không?"],
    ["Thẻ dùng được cho giao thông công cộng ở nước ngoài không?"],
    ["Thanh toán không tiếp xúc dùng được ở nước ngoài không?"],

    # --- campaign scenarios (the client's three) ---
    ["Làm sao để kích hoạt thẻ mới?", INACTIVE],
    ["Thẻ mới về rồi, tôi bắt đầu dùng thế nào?", INACTIVE],
    ["Có ưu đãi nào cho tôi không?", TRAVEL],
    ["Tôi có đủ điều kiện nâng hạng thẻ không?", TRAVEL],
    ["Có khuyến mãi nào cho tôi không?", DORMANT],
    ["Thẻ lâu rồi tôi không dùng, còn dùng được không?", DORMANT],

    # --- handoff offered, then accepted / declined ---
    ["Do you offer safe deposit boxes?", "có"],
    ["Tôi mua tiền điện tử trên app được không?", "không, cảm ơn", "What are your branch hours?"],
    ["@agent tôi có vấn đề về hoá đơn"],

    # --- explicit handoff requests ---
    ["Tôi muốn gặp nhân viên"],
    ["Cho tôi gặp người thật về giao dịch bị trừ hai lần"],

    # --- verification failure, then handoff ---
    ["Kiểm tra số dư", "1111 2222", "3333 4444", "5555 6666"],
]


def seed(router: Router) -> dict:
    """Replay every conversation through `router`. Returns a short summary.

    Resets the database first. These conversations perform real account
    actions now - one of them reports a card lost, which blocks it and issues a
    replacement - so without a reset each press of "Seed demo traffic" would
    leave the demo data further from its starting state than the last. A seed
    that degrades what it seeds is worse than no seed.
    """
    db.reset()
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
