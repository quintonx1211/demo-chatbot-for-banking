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
    "**Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ "
    "quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ "
    "vay, và giải đáp về sản phẩm, biểu phí của ngân hàng.\n\n"
    "Quý khách cần hỗ trợ gì ạ?"
)

SMALLTALK = (
    "Tôi vẫn đang lắng nghe quý khách đây ạ. Quý khách cần hỏi thêm điều gì không?"
)

# "Bạn là ai?" / "người hay máy?" - giới thiệu đúng danh tính, không để câu hỏi
# này rơi xuống RAG (lôi nhầm tài liệu) hay escalation.
ASSISTANT_IDENTITY = (
    "Dạ, tôi là **Trợ lý ảo của Ngân hàng ABC** - một trợ lý tự động (không phải "
    "người thật) hỗ trợ quý khách qua kênh này. Tôi giúp được: kiểm tra số dư và "
    "giao dịch, khóa/mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu "
    "phí của ngân hàng. Khi cần, tôi sẽ kết nối quý khách với chuyên viên.\n\n"
    "Quý khách cần hỗ trợ gì ạ?"
)

GOODBYE = (
    "Cảm ơn quý khách đã sử dụng dịch vụ hôm nay. Chúc quý khách một ngày tốt lành!"
)

# Khách từ chối / đang bận (TH3). Không nài ép: cảm ơn, để ngỏ cho lần sau.
DECLINE_GRACEFUL = (
    "Dạ vâng, tôi không làm phiền quý khách thêm ạ. Khi nào cần tìm hiểu về thẻ "
    "hay cần hỗ trợ tài khoản, quý khách cứ nhắn tôi bất cứ lúc nào nhé. "
    "Chúc quý khách một ngày tốt lành!"
)

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
    "Quý khách vui lòng cho tôi **số điện thoại đã đăng ký** (đủ 10 số) nhé."
)

VERIFICATION_ASK_CCCD = (
    "Cảm ơn quý khách. Quý khách cho tôi thêm **số CCCD/CMND** (đủ 12 số) "
    "để hoàn tất xác minh nhé."
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

CARD_CLOSE_CANCELLED = (
    "Tôi đã huỷ yêu cầu - thẻ của quý khách vẫn hoạt động bình thường."
)
CARD_CLOSE_UNCLEAR = (
    "Quý khách xác nhận muốn đóng thẻ này không ạ? Vui lòng trả lời có hoặc không."
)
CARD_CLOSE_NONE = "Tôi không tìm thấy thẻ nào trên hồ sơ của quý khách."
CARD_CLOSE_ALREADY_CLOSED = "Thẻ này đã được đóng từ trước đó."
CARD_CLOSE_CONFIRM_ASK = (
    "Quý khách có chắc muốn đóng thẻ này không? Vui lòng xác nhận có hoặc không."
)

LIMIT_INVALID = (
    "Tôi chưa nhận được số hạn mức hợp lệ, quý khách vui lòng gửi lại giúp tôi nhé."
)
LIMIT_ASK_AMOUNT = "Quý khách muốn nâng hạn mức mới lên bao nhiêu ạ?"

# -- account flows: the "nothing to show" branches ---------------------------

OFFERS_NONE = (
    "Quý khách hiện chưa có ưu đãi nào. Tôi có thể hỗ trợ thêm gì cho quý khách không?"
)
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

CROSS_SELL_ASK_INTEREST_UNVERIFIED = "Quý khách thường mua sắm trên nền tảng nào, hoặc chi tiêu nhiều nhất ở lĩnh vực nào ạ?"

# Consultative discovery: shown once when a customer opens the cross-sell topic,
# before any card is pitched. Understand the need first, then recommend on the
# next turn - a customer who just said "tôi hay chơi golf" should not be told to
# open a card before we know anything else about how they spend.
CROSS_SELL_DISCOVER = (
    "Dạ, cảm ơn quý khách đã chia sẻ. Để gợi ý thẻ phù hợp nhất, "
    "cho tôi hỏi thêm một chút ạ:\n\n"
    "- Hằng ngày quý khách chi tiêu nhiều nhất cho khoản nào "
    "(ăn uống, mua sắm online, di chuyển, du lịch...)?\n"
    "- Quý khách ưu tiên **hoàn tiền cho chi tiêu hằng ngày**, hay các "
    "**đặc quyền du lịch – cao cấp** (dặm bay, golf, phòng chờ sân bay) ạ?"
)

# Shown when the customer's stated interest matches no card's reward lines
# strongly enough to single one out. Point at the full line-up and ask for a bit
# more, instead of re-asking the same generic question.
CROSS_SELL_NO_OPEN_MATCH = (
    "Với nhu cầu quý khách vừa chia sẻ, tôi chưa khoanh được đúng một dòng thẻ. "
    "Ngân hàng có 4 dòng: **Classic** (mua sắm online, chi tiêu hằng ngày), "
    "**Platinum** (ăn uống, giải trí, di chuyển), **Signature** (tự chọn hoàn tiền, "
    "du lịch) và **Infinite** (du lịch cao cấp, dặm bay, golf).\n\n"
    "Quý khách quan tâm dòng nào, hay cho tôi biết thêm quý khách hay chi tiêu cho "
    "khoản gì để tôi tư vấn đúng ạ?"
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

ESCALATION_DECLINED = (
    "Vâng ạ, tôi tiếp tục hỗ trợ quý khách. Quý khách cần hỏi thêm điều gì không?"
)

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
        name: value
        for name, value in globals().items()
        if name.isupper() and isinstance(value, str)
    }
