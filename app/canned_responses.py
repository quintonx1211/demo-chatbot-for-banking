"""Registry of every fixed, no-interpolation reply the assistant sends.

Single source of truth for two consumers that must never drift apart:
`flows.py`, `router.py` and `guardrails.py` use these constants as the actual
reply text, and `pregenerate_voice.py` synthesizes audio for every one of them
ahead of time. A reply defined here and spoken from here is guaranteed to be
the exact text a customer reads on screen - there is no second copy of the
wording anywhere to fall out of sync with what gets said out loud.

Only fully static text belongs in this file: nothing with a customer name, an
account balance, a card mask, a reference number or any other per-conversation
value. Those can't be pre-recorded - the entire reason this file exists is
that a fixed string, unlike a generated one, is exactly repeatable, which is
what makes pre-generating its voice worthwhile instead of wasted disk space.
Templates that mix static wrapper text with a dynamic value (the balance
reply, a card confirmation with its mask) stay written inline where they are
used; only their fully-static siblings (the "no card to freeze" message next
to the "confirm to freeze" template, for instance) move here.
"""

from __future__ import annotations

# -- greetings and small talk ---------------------------------------------

GREETING = (
    "Trợ lý ảo ABC Bank hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách "
    "kiểm tra số dư và giao dịch, khóa thẻ khi bị mất, tra cứu hồ sơ vay, "
    "so sánh sản phẩm, xem ưu đãi thẻ, hay điều chỉnh hạn mức. "
    "Quý khách cần hỗ trợ gì ạ?"
)

SMALLTALK = "Tôi vẫn đang lắng nghe quý khách đây ạ. Quý khách cần hỏi thêm điều gì không?"

GOODBYE = "Cảm ơn quý khách đã sử dụng dịch vụ hôm nay. Kính chúc quý khách một ngày tốt lành!"

HUMAN_AGENT_HANDOFF = "Vâng ạ, tôi xin kết nối quý khách với chuyên viên ngay."

LEAVE_NOOP = "Quý khách đang trò chuyện với Trợ lý ảo ABC Bank. Tôi có thể hỗ trợ gì cho quý khách?"

LEFT_AGENT_NO_NAME = (
    "Quý khách đã quay lại với trợ lý ảo, tôi xin đưa quý khách ra khỏi hàng chờ. "
    "Tôi có thể hỗ trợ gì tiếp theo cho quý khách?"
)

# -- identity verification --------------------------------------------------
# Two steps, asked one at a time - not two codes in one message. Step 1 finds
# the customer by phone; step 2, only ever reached once step 1 succeeds,
# confirms it's really them with the national ID. See
# `flows._verify_phone_step` / `_verify_cccd_step`.

VERIFICATION_PROMPT = (
    "Tôi rất sẵn lòng hỗ trợ quý khách. Trước tiên, tôi cần xác minh nhanh "
    "để đảm bảo đúng là quý khách.\n\n"
    "Quý khách vui lòng cho tôi xin **số điện thoại đã đăng ký** (đủ 10 số)."
)

VERIFICATION_ASK_CCCD = (
    "Cảm ơn quý khách. Xin quý khách cho tôi thêm **số CCCD/CMND** (đủ 12 số) "
    "để hoàn tất xác minh."
)

VERIFICATION_PHONE_FORMAT_RETRY = (
    "Thông tin chưa đúng định dạng - tôi cần đúng **10 số** "
    "điện thoại, không có khoảng trắng hay ký tự khác. "
)

VERIFICATION_CCCD_FORMAT_RETRY = (
    "Thông tin chưa đúng định dạng - tôi cần đúng **12 số** "
    "CCCD/CMND.\n\n"
    "Quý khách vui lòng không gửi số thẻ đầy đủ hay mã PIN - tôi không bao giờ cần những thông tin đó."
)

VERIFICATION_FAILED = (
    "Tôi xin lỗi vì chưa xác minh được thông tin của quý khách, "
    "nên không thể tiếp tục thử thêm. Tôi xin kết nối quý khách "
    "với chuyên viên hỗ trợ trực tiếp."
)

# -- card actions: the fully-static branches only ----------------------------
# (the "confirm {type} {mask}" / "done - {mask}" templates stay inline, since
# they always carry the customer's own card data)

CARD_NONE_LEFT_REPORT_LOST = "Tôi đã kiểm tra, hiện quý khách không có thẻ nào để khóa."
CARD_NONE_LEFT_FREEZE = "Quý khách hiện không có thẻ nào đang hoạt động để tạm khóa."
CARD_NONE_LEFT_UNFREEZE = (
    "Hiện không có thẻ nào đang tạm khóa. Nếu thẻ đã được báo mất "
    "thì đã bị khóa vĩnh viễn, không thể mở lại - "
    "tôi có thể kiểm tra thẻ thay thế giúp quý khách."
)
CARD_ACTION_CANCELLED = "Vâng ạ, tôi giữ nguyên trạng thái thẻ cho quý khách."

CARD_CLOSE_CANCELLED = "Tôi đã huỷ yêu cầu - thẻ của quý khách vẫn hoạt động bình thường."
CARD_CLOSE_UNCLEAR = "Quý khách xác nhận muốn đóng thẻ này không ạ? Vui lòng trả lời có hoặc không."
CARD_CLOSE_NONE = "Tôi không tìm thấy thẻ nào trên hồ sơ của quý khách."
CARD_CLOSE_ALREADY_CLOSED = "Thẻ này đã được đóng từ trước đó."
CARD_CLOSE_CONFIRM_ASK = "Quý khách có chắc muốn đóng thẻ này không? Vui lòng xác nhận có hoặc không."

LIMIT_INVALID = "Tôi chưa nhận được số hạn mức hợp lệ, quý khách vui lòng gửi lại giúp tôi."
LIMIT_ASK_AMOUNT = "Quý khách muốn nâng hạn mức mới lên bao nhiêu ạ?"

# -- account flows: the "nothing to show" branches ---------------------------

OFFERS_NONE = "Quý khách hiện chưa có ưu đãi nào. Tôi có thể hỗ trợ thêm gì cho quý khách không?"
ACTIVATION_NONE_PENDING = (
    "Các thẻ trong hồ sơ của quý khách đều đã được kích hoạt. Nếu có "
    "giao dịch nào bị từ chối, có thể do nguyên nhân khác - "
    "tôi sẽ kiểm tra giúp quý khách."
)
NO_LOAN_ON_FILE = (
    "Tôi không thấy hồ sơ vay nào đang mở trên tài khoản của quý khách. "
    "Nếu quý khách vừa nộp hồ sơ tại chi nhánh trong 24 giờ qua, có thể hệ thống "
    "chưa kịp đồng bộ - tôi xin chuyển quý khách sang bộ phận tín dụng kiểm tra lại."
)
NO_TRANSACTIONS = "Tôi không thấy giao dịch gần đây nào trên tài khoản của quý khách."

CROSS_SELL_ASK_INTEREST_UNVERIFIED = (
    "Quý khách thường mua sắm trên nền tảng nào, hoặc chi tiêu nhiều nhất ở lĩnh vực nào ạ?"
)

# -- escalation / handoff (router.py) ----------------------------------------

ESCALATION_MESSAGE = (
    "Tôi xin kết nối quý khách với một chuyên viên - chuyên viên sẽ thấy đầy đủ nội dung cuộc "
    "trò chuyện này nên quý khách không cần giải thích lại.\n\n"
    "**Quý khách đang trong hàng chờ nhân viên hỗ trợ.**"
)

REQUEUED_MESSAGE = (
    "Câu hỏi này cũng nằm ngoài phạm vi tôi có thể trả lời - tôi đã ghi chú lại để "
    "chuyên viên nắm khi tiếp nhận quý khách. **Quý khách vẫn đang trong hàng chờ.**"
)

ESCALATION_OFFER_BODY = (
    "Thành thật mà nói, tôi chưa có thông tin chính xác về vấn đề này.\n\n"
    "**Quý khách có muốn tôi kết nối với một chuyên viên để hỗ trợ thêm không?** "
    "Chuyên viên sẽ thấy đầy đủ nội dung cuộc trò chuyện này nên quý khách không cần giải thích lại. "
    "Hoặc quý khách có thể hỏi tôi câu khác."
)

ESCALATION_DECLINED = "Vâng ạ, tôi tiếp tục hỗ trợ quý khách. Quý khách cần hỏi thêm điều gì không?"

# -- compliance guardrail (guardrails.py) ------------------------------------

RESTRICTED_RESPONSE = (
    "Tôi xin phép không tư vấn về đầu tư, thuế hay pháp lý - những vấn đề này "
    "cần chuyên gia có chứng chỉ hành nghề mới đưa ra được thông tin chính xác. Tôi có thể "
    "kết nối quý khách với chuyên viên ngân hàng để trao đổi về sản phẩm, hoặc hỗ trợ "
    "quý khách các câu hỏi về tài khoản, thẻ hay hồ sơ."
)


def all_responses() -> dict[str, str]:
    """Every constant in this module, name -> text.

    What `pregenerate_voice.py` iterates over. Reads the module's own
    namespace rather than keeping a second, hand-maintained list - so a new
    constant added above is picked up for pre-generation automatically, and
    one nobody added here (a template with a placeholder, correctly left
    inline elsewhere) never accidentally gets synthesized as if it were
    static.
    """
    return {
        name: value for name, value in globals().items()
        if name.isupper() and isinstance(value, str)
    }
