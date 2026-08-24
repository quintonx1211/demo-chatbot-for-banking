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
    "Xin chào! Mình là Linh, trợ lý ảo của Ngân hàng ABC. Mình có thể "
    "giúp bạn kiểm tra số dư và giao dịch, khóa thẻ khi bị mất, tra cứu hồ sơ vay, "
    "so sánh sản phẩm, xem ưu đãi thẻ, hay điều chỉnh hạn mức. "
    "Bạn đang cần hỗ trợ gì vậy?"
)

SMALLTALK = "Mình đây, mình vẫn đang lắng nghe bạn nè. Bạn cần hỏi thêm gì không?"

GOODBYE = "Rất vui vì đã giúp được bạn hôm nay. Chúc bạn một ngày thật vui vẻ nhé!"

HUMAN_AGENT_HANDOFF = "Dạ được, để mình kết nối bạn với chuyên viên ngay đây."

LEAVE_NOOP = "Bạn đang trò chuyện với Linh đây ạ. Mình có thể giúp gì cho bạn?"

LEFT_AGENT_NO_NAME = (
    "Bạn đã quay lại với mình rồi nè, mình đưa bạn ra khỏi hàng chờ luôn. "
    "Mình có thể giúp gì cho bạn tiếp theo?"
)

# -- identity verification --------------------------------------------------
# Two steps, asked one at a time - not two codes in one message. Step 1 finds
# the customer by phone; step 2, only ever reached once step 1 succeeds,
# confirms it's really them with the national ID. See
# `flows._verify_phone_step` / `_verify_cccd_step`.

VERIFICATION_PROMPT = (
    "Mình rất sẵn lòng hỗ trợ bạn! Trước tiên mình cần xác minh nhanh "
    "để chắc chắn đúng là bạn nhé.\n\n"
    "Bạn cho mình xin **số điện thoại đã đăng ký** (đủ 10 số) được không ạ?"
)

VERIFICATION_ASK_CCCD = (
    "Cảm ơn bạn. Giờ bạn cho mình xin thêm **số CCCD/CMND** (đủ 12 số) "
    "để hoàn tất xác minh nhé."
)

VERIFICATION_PHONE_FORMAT_RETRY = (
    "Hình như chưa đúng định dạng rồi bạn ơi - mình cần đúng **10 số** "
    "điện thoại thôi nhé, không có khoảng trắng hay ký tự khác. "
    "Ví dụ: `0912345678`."
)

VERIFICATION_CCCD_FORMAT_RETRY = (
    "Hình như chưa đúng định dạng rồi bạn ơi - mình cần đúng **12 số** "
    "CCCD/CMND thôi nhé.\n\n"
    "Bạn đừng gửi số thẻ đầy đủ hay mã PIN nha - mình không bao giờ cần những thông tin đó đâu."
)

VERIFICATION_FAILED = (
    "Mình xin lỗi vì chưa xác minh được thông tin của bạn, "
    "nên mình không thể thử thêm nữa. Để mình kết nối bạn "
    "với chuyên viên hỗ trợ trực tiếp nhé."
)

# -- card actions: the fully-static branches only ----------------------------
# (the "confirm {type} {mask}" / "done - {mask}" templates stay inline, since
# they always carry the customer's own card data)

CARD_NONE_LEFT_REPORT_LOST = "Mình kiểm tra rồi, hiện bạn không có thẻ nào để khóa cả."
CARD_NONE_LEFT_FREEZE = "Bạn hiện không có thẻ nào đang hoạt động để tạm khóa cả."
CARD_NONE_LEFT_UNFREEZE = (
    "Hiện không có thẻ nào đang tạm khóa cả bạn ơi. Nếu thẻ đã bị báo mất "
    "thì sẽ khóa vĩnh viễn, không mở lại được - "
    "nhưng mình có thể kiểm tra thẻ thay thế giúp bạn."
)
CARD_ACTION_CANCELLED = "Dạ được, mình giữ nguyên trạng thái thẻ cho bạn nhé."

CARD_CLOSE_CANCELLED = "Mình đã huỷ yêu cầu rồi - thẻ của bạn vẫn hoạt động bình thường như cũ nhé."
CARD_CLOSE_UNCLEAR = "Bạn xác nhận muốn đóng thẻ này chứ? Trả lời giúp mình có hoặc không nhé."
CARD_CLOSE_NONE = "Mình không tìm thấy thẻ nào trên hồ sơ của bạn cả."
CARD_CLOSE_ALREADY_CLOSED = "Thẻ này đóng rồi bạn ơi, từ trước đó."
CARD_CLOSE_CONFIRM_ASK = "Bạn có chắc muốn đóng thẻ này không? Xác nhận giúp mình có hoặc không nhé."

LIMIT_INVALID = "Mình chưa nhận được số hạn mức hợp lệ, bạn thử gửi lại giúp mình nhé."
LIMIT_ASK_AMOUNT = "Bạn muốn nâng hạn mức mới lên bao nhiêu ạ?"

# -- account flows: the "nothing to show" branches ---------------------------

OFFERS_NONE = "Bạn hiện chưa có ưu đãi nào để mình gửi hôm nay cả. Bạn cần mình hỗ trợ thêm gì không?"
ACTIVATION_NONE_PENDING = (
    "Thẻ trong hồ sơ của bạn đều đã được kích hoạt hết rồi đó. Nếu có "
    "giao dịch nào bị từ chối, chắc là do nguyên nhân khác - "
    "mình kiểm tra giúp bạn nhé."
)
NO_LOAN_ON_FILE = (
    "Mình không thấy hồ sơ vay nào đang mở trên tài khoản của bạn cả. "
    "Nếu bạn vừa nộp hồ sơ tại chi nhánh trong 24 giờ qua thì có thể hệ thống "
    "chưa kịp đồng bộ - mình chuyển bạn qua bộ phận tín dụng kiểm tra lại nhé."
)
NO_TRANSACTIONS = "Mình không thấy giao dịch gần đây nào trên tài khoản của bạn cả."

CROSS_SELL_ASK_INTEREST_UNVERIFIED = (
    "Bạn hay mua sắm trên nền tảng nào, hoặc thường chi tiêu nhiều nhất ở đâu vậy?"
)

# -- escalation / handoff (router.py) ----------------------------------------

ESCALATION_MESSAGE = (
    "Để mình kết nối bạn với một chuyên viên nhé - họ sẽ thấy đầy đủ nội dung cuộc "
    "trò chuyện này nên bạn không cần giải thích lại đâu.\n\n"
    "**Bạn đang trong hàng chờ nhân viên hỗ trợ.**"
)

REQUEUED_MESSAGE = (
    "Câu này cũng nằm ngoài những gì mình trả lời được - mình đã ghi chú lại để "
    "chuyên viên nắm khi tiếp nhận bạn nhé. **Bạn vẫn đang trong hàng chờ.**"
)

ESCALATION_OFFER_BODY = (
    "Thành thật là mình chưa có thông tin chính xác về vấn đề này.\n\n"
    "**Bạn có muốn mình kết nối với một chuyên viên để hỗ trợ thêm không?** "
    "Họ sẽ thấy đầy đủ nội dung cuộc trò chuyện này nên bạn không cần giải thích lại đâu. "
    "Hoặc bạn cứ hỏi mình câu khác cũng được."
)

ESCALATION_DECLINED = "Dạ được, vậy mình tiếp tục hỗ trợ bạn nhé. Bạn cần hỏi thêm gì không?"

# -- compliance guardrail (guardrails.py) ------------------------------------

RESTRICTED_RESPONSE = (
    "Mình xin phép không tư vấn về đầu tư, thuế hay pháp lý nhé - những vấn đề này "
    "cần chuyên gia có chứng chỉ hành nghề đưa ra mới chuẩn xác được. Mình có thể "
    "kết nối bạn với chuyên viên ngân hàng để trao đổi về sản phẩm, hoặc mình trả lời "
    "giúp bạn các câu hỏi về tài khoản, thẻ hay hồ sơ nhé."
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
