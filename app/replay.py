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

_FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "accounts.json").read_text(
        encoding="utf-8"
    )
)["customers"]
_BY_PROFILE = {c["profile"]: c for c in _FIXTURE}


def cred(profile: str) -> tuple[str, str]:
    """The two verification turns for the customer playing `profile` - phone
    number first, then national ID, sent as separate messages because that is
    how the flow actually asks for them now. Splice into a conversation with
    `*cred(...)`, not `cred(...)` alone."""
    customer = _BY_PROFILE[profile]
    return (customer["phone"], customer["national_id"])


TRAVEL = cred("travel_offer")
DORMANT = cred("dormant_card")
INACTIVE = cred("inactive_card")
LOAN = cred("loan_in_review")
MORTGAGE = cred("mortgage_approved")
FROZEN = cred("frozen_card")

CONVERSATIONS: list[list[str]] = [
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
    ["Xin chào", "Phí chuyển tiền quốc tế thế nào?", "Cảm ơn, vậy thôi ạ"],
    ["Chào bạn", "Tôi muốn tra soát một giao dịch lạ", "tạm biệt"],
    ["Nếu tài khoản bị âm thì sao?", "Có giới hạn số lần không?"],
    ["Kiểm tra số dư", *TRAVEL],
    ["Xem giao dịch gần đây", *DORMANT],
    ["Hồ sơ vay của tôi đến đâu rồi?", *LOAN],
    ["Hồ sơ vay mua nhà của tôi thế nào?", *MORTGAGE],
    ["Tôi là ai trong hệ thống?", *TRAVEL],
    ["Tôi làm mất thẻ ghi nợ", *DORMANT, "có"],
    ["tạm khoá thẻ giúp tôi", *TRAVEL, "có"],
    ["mở khoá thẻ giúp tôi", *FROZEN, "có"],
    ["Tôi làm mất thẻ ghi nợ", *TRAVEL, "thôi bỏ đi, chi nhánh mở cửa mấy giờ?"],
    ["Tôi có nên đầu tư tiết kiệm vào cổ phiếu công nghệ không?"],
    ["Bitcoin bây giờ có đáng đầu tư không?"],
    ["Làm sao để giảm tiền thuế phải nộp?"],
    ["Ngân hàng có bảo hiểm cây trồng cho vườn nho không?"],
    ["Tôi mua tiền điện tử trên app được không?"],
    ["Ngân hàng có dịch vụ két sắt an toàn không?"],
    ["Thẻ có kèm bảo hiểm du lịch không?"],
    ["Thẻ tín dụng của tôi có bảo hiểm du lịch không?"],
    ["Thẻ của tôi có được bảo hiểm du lịch không?"],
    ["Bảo hiểm du lịch đi kèm thẻ gồm những gì?"],
    ["Tôi mua thêm bảo hiểm du lịch cho thẻ được không?"],
    ["Thẻ của tôi dùng được ở tàu điện Tokyo không?"],
    ["Thẻ dùng được cho giao thông công cộng ở nước ngoài không?"],
    ["Thanh toán không tiếp xúc dùng được ở nước ngoài không?"],
    ["Làm sao để kích hoạt thẻ mới?", *INACTIVE],
    ["Thẻ mới về rồi, tôi bắt đầu dùng thế nào?", *INACTIVE],
    ["Có ưu đãi nào cho tôi không?", *TRAVEL],
    ["Tôi có đủ điều kiện nâng hạng thẻ không?", *TRAVEL],
    ["Có khuyến mãi nào cho tôi không?", *DORMANT],
    ["Thẻ lâu rồi tôi không dùng, còn dùng được không?", *DORMANT],
    ["Do you offer safe deposit boxes?", "có"],
    [
        "Tôi mua tiền điện tử trên app được không?",
        "không, cảm ơn",
        "What are your branch hours?",
    ],
    ["@agent tôi có vấn đề về hoá đơn"],
    ["Tôi muốn gặp nhân viên"],
    ["Cho tôi gặp người thật về giao dịch bị trừ hai lần"],
    ["Kiểm tra số dư", "0911111111", "0922222222", "0933333333"],
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
