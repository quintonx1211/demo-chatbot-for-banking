"""Deterministic scripted flows for anything that touches an account.

No model output reaches the customer from this module. Every response is
assembled from templates and data read out of the system of record, which is
what makes balance checks, card blocks and loan lookups auditable and
reproducible. Flows that act on an account first drive a verification
sub-flow via slot filling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import canned_responses as R
from . import cards, db, memory
from .campaigns import CampaignBook
from .nlu import PERSONAL_INTENTS
from .rules_engine import ENGINE
from .session import Session

CAMPAIGNS = CampaignBook()

# Flows that must not run for an unverified caller.
PROTECTED_FLOWS = PERSONAL_INTENTS


@dataclass
class FlowResult:
    text: str
    handled: bool = True
    escalate: bool = False
    escalation_reason: str | None = None
    note: str = ""


def _first_name(customer: dict) -> str:
    """How to address this customer, read from the record, never inferred."""
    return customer.get("given_name") or customer["name"].split()[-1]


def _full_name(customer: dict) -> str:
    """The customer's full name as written on the record (họ tên đầy đủ)."""
    return customer["name"]


def _money(amount: float, currency: str = "VND") -> str:
    """Format money the way the currency is actually written.

    VND has no minor unit in practice, so printing two decimal places on a
    balance of 41,238,000 is not just noise - it reads as a different kind of
    number and invites the customer to check a rounding that does not exist.
    """
    if currency == "VND":
        return f"{amount:,.0f} VND"
    return f"${amount:,.2f}"


# -- verification ---------------------------------------------------------


def _verification_prompt(session: Session, intent: str) -> FlowResult:
    """Start the identity check before a protected flow runs: full phone
    number first, full national ID (CCCD) only once the phone has already
    matched someone. See `_verify_phone_step` / `_verify_cccd_step`.

    Sequential rather than "send both at once": a real phone number and CCCD
    are long enough (10 and 12 digits) that pasting them as one message is
    awkward to get right, and asking one at a time is the pattern a customer
    already expects from step-up auth elsewhere. The trade-off, stated
    plainly: a wrong CCCD after a correct phone number does reveal that the
    phone number itself was real, which the old two-at-once, one generic
    "doesn't match" design deliberately did not. At 10^10 phone numbers that
    is not a meaningful brute-force surface the way a 4-digit code was.
    """
    session.pending_flow = "verify"
    session.slots["target_intent"] = intent
    session.slots["verify_stage"] = "phone"
    return FlowResult(text=R.VERIFICATION_PROMPT, note="verification_started")


def _handle_verification(session: Session, text: str) -> FlowResult:
    if session.slots.get("verify_stage") == "cccd":
        return _verify_cccd_step(session, text)
    return _verify_phone_step(session, text)


def _digits_only(text: str) -> str:
    return re.sub(r"[^0-9]", "", text)


def _verify_phone_step(session: Session, text: str) -> FlowResult:
    # The message has to *be* the phone number, not merely contain it: strip
    # everything that is not a digit and the result must be *exactly* 10
    # characters, no more and no fewer. "Số điện thoại của tôi là 0912345678
    # ạ" passes (the words contribute no digits); a pasted 16-digit card
    # number, or the real phone number with a PIN or CCCD tacked on, does
    # not - both leave more than 10 digits behind, which a customer merely
    # containing the right code inside a longer paste would otherwise slip
    # through.
    digits = _digits_only(text)
    if len(digits) != 10:
        return FlowResult(
            text=R.VERIFICATION_PHONE_FORMAT_RETRY, note="verification_retry"
        )

    match = db.find_by_phone(digits)
    if not match:
        return _verification_retry_or_fail(session)

    session.slots["verify_candidate_id"] = match["customer_id"]
    session.slots["verify_stage"] = "cccd"
    return FlowResult(text=R.VERIFICATION_ASK_CCCD, note="verification_phone_matched")


def _verify_cccd_step(session: Session, text: str) -> FlowResult:
    digits = _digits_only(text)
    if len(digits) != 12:
        return FlowResult(
            text=R.VERIFICATION_CCCD_FORMAT_RETRY, note="verification_retry"
        )

    candidate_id = session.slots.get("verify_candidate_id")
    if not candidate_id or not db.check_national_id(candidate_id, digits):
        # Stays on the CCCD step against the same candidate rather than
        # bouncing back to re-ask the phone number: the phone already matched
        # a real record, so re-typing the same digits again would not add
        # anything - the 3-attempt budget is what actually limits guessing.
        return _verification_retry_or_fail(session)

    match = db.get_customer(candidate_id)
    session.customer_id = match["customer_id"]
    session.verified = True
    target = session.slots.get("target_intent")
    session.reset_flow()

    # Cross-session recall lands here and nowhere earlier: before this line the
    # session has no verified customer, so there is nobody to remember.
    recalled = memory.store.summary(match["customer_id"])
    greeting = (
        f"Cảm ơn quý khách {_full_name(match)}, quý khách đã xác minh thành công. "
    )
    if recalled:
        greeting += recalled + " "

    # Scenario 2 – Automated Cross-Selling: raise campaign offer immediately
    # after identity is established so the customer doesn't have to ask.
    # handle() runs first so that flows like _card_offers can set campaign_offered
    # before proactive_offer checks it — preventing the same offer from appearing twice.
    if target:
        follow_up = handle(session, target, "")
        proactive = proactive_offer(session)
        proactive_text = proactive.text if proactive else ""
        return FlowResult(
            text=greeting + follow_up.text + proactive_text,
            escalate=follow_up.escalate,
            escalation_reason=follow_up.escalation_reason,
            note="verified_then_" + target,
        )
    proactive = proactive_offer(session)
    proactive_text = proactive.text if proactive else ""
    return FlowResult(
        text=greeting
        + "Tôi có thể hỗ trợ gì tiếp theo cho quý khách?"
        + proactive_text,
        note="verified",
    )


def _verification_retry_or_fail(session: Session) -> FlowResult:
    """One failed attempt at either step, shared so the 3-strike budget is
    spent across the whole identity check, not reset by moving from the
    phone step to the CCCD step or back."""
    attempts = int(session.slots.get("verify_attempts", "0")) + 1
    session.slots["verify_attempts"] = str(attempts)
    if attempts >= 3:
        session.reset_flow()
        return FlowResult(
            text=R.VERIFICATION_FAILED,
            escalate=True,
            escalation_reason="Xác minh danh tính thất bại ba lần",
            note="verification_failed",
        )
    return FlowResult(
        text=(
            f"Thông tin chưa khớp với hồ sơ (lần thử {attempts}/3) - "
            "quý khách vui lòng thử lại."
        ),
        note="verification_retry",
    )


# -- account flows --------------------------------------------------------


def _balance(session: Session) -> FlowResult:
    customer = session.customer
    lines = [
        f"Đây là số dư tài khoản của quý khách tính đến hôm nay, {_first_name(customer)}:"
    ]
    for account in customer["accounts"]:
        lines.append(
            f"- **{account['type']}** {account['mask']} - "
            f"số dư {_money(account['balance'], account['currency'])}, "
            f"khả dụng {_money(account['available'], account['currency'])}"
        )
    return FlowResult(text="\n".join(lines), note="balance_read_from_core")


def _account_summary(session: Session) -> FlowResult:
    """Answer "who am I" from the record, once identity is verified.

    Previously this escalated: a reasonable question, asked by a customer the
    system had already verified, handed to a human for data it was holding.
    """
    customer = session.customer
    lines = [
        f"Quý khách là **{customer['name']}** ({customer['customer_id']}), "
        f"đã được xác minh trong cuộc hội thoại này.",
        "",
        "Đây là các sản phẩm quý khách đang có với chúng tôi:",
    ]
    for account in customer["accounts"]:
        lines.append(f"- **{account['type']}** {account['mask']}")
    for card in customer["cards"]:
        state = "" if card["status"] == "active" else f" - {card['status']}"
        lines.append(f"- **Thẻ {card['type']}** {card['mask']}{state}")
    for loan in customer.get("loans", []):
        lines.append(
            f"- **{loan['product']}** hồ sơ {loan['application_id']} "
            f"- {loan['status']}"
        )
    lines.append("")
    lines.append("Tôi có thể xem chi tiết về bất kỳ mục nào ở trên cho quý khách.")
    return FlowResult(text="\n".join(lines), note="account_summary_from_core")


def _activate_card(session: Session) -> FlowResult:
    """Card activation by redirection.

    The client chose redirection over a real-time API call, so this hands over
    a deeplink and an SMS fallback and stops. It deliberately does not collect
    an OTP: a chat window asking for a one-time passcode is indistinguishable
    from the phishing this bank warns its customers about, and teaching people
    that it is normal undoes the security advice in the knowledge base.
    """
    customer = session.customer
    inactive = [c for c in customer["cards"] if c["status"] == "inactive"]
    dormant = [c for c in customer["cards"] if c["status"] == "dormant"]
    campaign = CAMPAIGNS.campaigns.get("CARD-ACTIVATION", {})
    dormant_campaign = CAMPAIGNS.campaigns.get("DORMANT-REACTIVATION", {})

    if dormant:
        card = dormant[0]
        offer = dormant_campaign.get("offer", "")
        offer_line = f"\n\n**Ưu đãi kích hoạt lại:** {offer}" if offer else ""
        return FlowResult(
            text=(
                f"Thẻ **{card['type']} {card['mask']}** của quý khách đang ở trạng thái ngủ đông do "
                f"không có giao dịch trong thời gian dài.{offer_line}\n\n"
                f"**{dormant_campaign.get('cta', 'Thực hiện một giao dịch để kích hoạt lại')}** - "
                f"{dormant_campaign.get('deeplink', '')}\n\n"
                "_Không cần liên hệ chi nhánh - chỉ cần dùng thẻ là thẻ tự động hoạt động trở lại._"
            ),
            note=f"dormant_reactivation:{card['card_id']}",
        )

    if not inactive:
        return FlowResult(
            text=R.ACTIVATION_NONE_PENDING, note="activation_none_pending"
        )

    card = inactive[0]
    return FlowResult(
        text=(
            f"Thẻ **{card['type']} {card['mask']}** của quý khách đã được cấp nhưng chưa kích hoạt.\n\n"
            f"**{campaign.get('cta', 'Kích hoạt trong ứng dụng di động')}** - "
            f"{campaign.get('deeplink', '')}\n\n"
            f"Hoặc: {campaign.get('sms_alternative', 'gọi số điện thoại trên thẻ')}.\n\n"
            "_Tôi sẽ không bao giờ yêu cầu mã OTP trong cuộc hội thoại này. "
            "Nếu ai đó tự xưng là chúng tôi và yêu cầu mã OTP, đó không phải chúng tôi._"
        ),
        note=f"activation_redirect:{card['card_id']}",
    )


def _card_offers(session: Session) -> FlowResult:
    """Offers the CRM selected for this customer overnight.

    Nothing here is inferred. The assistant reads the eligibility row and
    presents it; deciding who is suitable for a financial product is the bank's
    job, made against its own data, and keeping that boundary is what separates
    a campaign assistant from an unlicensed adviser.
    """
    offers = CAMPAIGNS.offers_for(session.customer_id)
    if not offers:
        return FlowResult(text=R.OFFERS_NONE, note="offers_none")

    # Mark as shown so proactive_offer won't repeat the same offers below.
    session.slots["campaign_offered"] = "1"

    lines = ["Đây là các ưu đãi hiện có trên tài khoản của quý khách hôm nay:", ""]
    for offer in offers:
        lines.append(offer.body)
        if offer.deeplink:
            lines.append(f"→ {offer.cta}: {offer.deeplink}")
        lines.append("")
    lines.append(
        f"_Được hệ thống chiến dịch của ngân hàng chọn, cập nhật lần cuối "
        f"{CAMPAIGNS.age_hours:.0f} giờ trước._"
        if CAMPAIGNS.age_hours is not None
        else ""
    )
    return FlowResult(
        text="\n".join(l for l in lines if l is not None).strip(),
        note="offers:" + ",".join(o.campaign_id for o in offers),
    )


def proactive_offer(session: Session) -> FlowResult | None:
    """An offer worth raising unprompted, once identity is established.

    Activation and reactivation are raised because they are service-blocking.
    Cross-sell offers are also raised for customers in the bank's target list
    (Scenario 2 – Automated Cross-Selling): the bot proactively mentions the
    recommended card and then hands off to _cross_sell_interest to ask about
    spending interests before explaining the fit.
    """
    if session.slots.get("campaign_offered"):
        return None
    offers = [
        o
        for o in CAMPAIGNS.offers_for(session.customer_id)
        if o.type in ("activation", "reactivation", "cross_sell")
    ]
    if not offers:
        return None

    session.slots["campaign_offered"] = "1"
    offer = offers[0]
    return FlowResult(
        text=(
            f"\n\n**Nhân tiện:** **{offer.name}** – {offer.hook}\n\n"
            f"→ {offer.cta}: {offer.deeplink}"
        ),
        note=f"proactive:{offer.campaign_id}",
    )


def _transactions(session: Session) -> FlowResult:
    customer = session.customer
    transactions = customer["transactions"][:5]
    if not transactions:
        return FlowResult(text=R.NO_TRANSACTIONS, note="no_transactions")
    lines = ["Các giao dịch gần nhất của quý khách:"]
    for txn in transactions:
        sign = "+" if txn["amount"] > 0 else "−"
        lines.append(
            f"- {txn['date']} · {txn['description']} · "
            f"{sign}{_money(abs(txn['amount']))}"
        )
    return FlowResult(text="\n".join(lines), note="transactions_read_from_core")


def _loan_status(session: Session) -> FlowResult:
    customer = session.customer
    loans = customer.get("loans", [])
    if not loans:
        return FlowResult(text=R.NO_LOAN_ON_FILE, note="no_loan_on_file")
    lines = ["Đây là trạng thái hồ sơ vay của quý khách:"]
    for loan in loans:
        lines.append(
            f"- **{loan['product']}** ({loan['application_id']}) - "
            f"{_money(loan['amount'])}\n"
            f"  Trạng thái: **{loan['status']}** · nộp ngày {loan['submitted_on']}\n"
            f"  {loan['note']}"
        )
    return FlowResult(text="\n".join(lines), note="loan_read_from_core")


# What each customer-facing card action does, and what it may act on. Freezing
# and reporting a card lost are two different requests that the previous version
# collapsed into one "block", which is why a customer who froze a card had no
# way back: the only transition that existed was terminal.
CARD_ACTIONS: dict[str, dict] = {
    "report_lost": {
        "verb": "báo mất và khóa",
        "from": ("active", "frozen", "dormant", "inactive"),
        "reversible": False,
        "confirm": (
            "Xác nhận lại: báo mất và khóa **thẻ {type} {mask}** của quý khách?\n\n"
            "Thao tác này không thể hoàn tác - thẻ đã khóa sẽ không mở lại được, "
            "vì vậy tôi sẽ đặt thẻ thay thế cho quý khách cùng lúc. Nếu quý khách chỉ thất lạc "
            "tạm thời và nghĩ sẽ tìm lại được, quý khách có thể nói **tạm khóa** để có thể mở khóa sau."
        ),
        "none_left": R.CARD_NONE_LEFT_REPORT_LOST,
    },
    "freeze": {
        "verb": "tạm khóa",
        "from": ("active",),
        "reversible": True,
        "confirm": (
            "Tôi có thể tạm khóa **thẻ {type} {mask}** của quý khách ngay. "
            "Mọi giao dịch sẽ bị từ chối cho đến khi mở khóa lại, "
            "quý khách có thể thực hiện việc này bất cứ lúc nào tại đây.\n\nQuý khách vui lòng trả lời **có** để tạm khóa."
        ),
        "none_left": R.CARD_NONE_LEFT_FREEZE,
    },
    "unfreeze": {
        "verb": "mở khóa",
        "from": ("frozen",),
        "reversible": True,
        "confirm": (
            "Tôi có thể mở khóa **thẻ {type} {mask}** của quý khách ngay - thẻ sẽ hoạt động "
            "lại ngay lập tức.\n\nQuý khách vui lòng trả lời **có** để xác nhận."
        ),
        "none_left": R.CARD_NONE_LEFT_UNFREEZE,
    },
}

# Confirmation words in both languages. The Vietnamese alternatives are
# anchored to the start of the message while the English ones are not, and that
# asymmetry is deliberate: "có" and "không" are extremely common syllables
# inside ordinary Vietnamese sentences ("tôi không tìm thấy thẻ", "thẻ này có
# vấn đề"), so an unanchored match would read a confirmation out of a sentence
# that was not answering the question. English "yes"/"no" as whole words are
# far less ambiguous mid-sentence.
# A message naming a concrete spending / lifestyle interest. Its only job is to
# decide "does the customer already have an interest we can recommend against?"
# so the unverified cross-sell path never re-asks someone who just told us. Kept
# broad on purpose - it includes interests no open card covers (golf, du lịch
# nước ngoài); those still count as "stated", they just resolve to the open
# catalogue reply rather than a re-prompt.
_STATED_INTEREST_RE = re.compile(
    r"(mua sắm|mua hàng|online|thương mại điện tử|tmđt|shopee|lazada|tiki|"
    r"ăn uống|nhà hàng|ăn nhà hàng|quán ăn|cà ?phê|cafe|coffee|"
    r"du lịch|nước ngoài|đi nước ngoài|khách sạn|vé máy bay|máy bay|"
    r"di chuyển|gọi xe|grab|be\b|xanh sm|taxi|xăng|"
    r"giải trí|rạp|phim|xem phim|xem ca nhạc|"
    r"golf|thể thao|gym|tập gym|"
    r"thời trang|quần áo|mỹ phẩm|siêu thị|tạp hóa|"
    r"chi tiêu|thanh toán|quẹt thẻ|cà thẻ|cà the|cavet)",
    re.IGNORECASE,
)

# Reward preference, the second half of a discovery answer ("ưu tiên hoàn
# tiền", "tích điểm"). On its own this classifies as reward_inquiry, which would
# knock the customer out of the cross-sell flow mid-question - see
# is_cross_sell_answer.
_REWARD_PREF_RE = re.compile(
    r"(hoàn tiền|tích điểm|điểm thưởng|ưu đãi|ưu tiên|cashback|points?|dặm|miles)",
    re.IGNORECASE,
)


# Câu từ chối lọt vào cross-sell (TH3, không nài ép): "không muốn/cần/quan tâm",
# "không có nhu cầu", "thôi...không", "bận họp/rồi", "để sau". KHÔNG bắt "bận rộn".
_NO_INTEREST_RE = re.compile(
    r"(không|đừng|chẳng|chả|chưa)\s*(muốn|cần|thích|quan tâm)"
    r"|không\s+có\s+nhu\s+cầu"
    r"|không\s+(mở|đăng\s*ký)"
    r"|\bthôi\b[^.]*\b(không|khỏi)\b"
    r"|bận\s+(họp|rồi|lắm|quá)|đang\s+bận"
    r"|để\s+(sau|dịp\s+khác|lúc\s+khác|hôm\s+khác|khi\s+khác)",
    re.IGNORECASE,
)

# Câu hỏi thủ tục/điều kiện mở thẻ (TH1) - lọt vào cross-sell vì có "mở thẻ", nên
# bắt tại đây để trả lời thủ tục thay vì hỏi lại thói quen chi tiêu.
_APPLICATION_RE = re.compile(
    r"(giấy tờ|hồ sơ|điều kiện|thủ tục|chứng minh thu nhập|cần chứng minh|"
    r"làm sao (để )?mở|làm thế nào (để )?mở|cách (để )?mở)",
    re.IGNORECASE,
)


def is_cross_sell_answer(text: str) -> bool:
    """True if `text` reads as an answer to the cross-sell discovery question - a
    spending interest or a reward preference. The router uses this to keep the
    pending cross_sell_interest flow instead of re-routing the answer to
    reward_inquiry or the knowledge base."""
    return bool(_STATED_INTEREST_RE.search(text) or _REWARD_PREF_RE.search(text))


_YES_RE = re.compile(
    r"(\b(yes|yeah|yep|confirm|correct|do it|go ahead)\b"
    r"|^\s*(có|dạ|vâng|ừ|ok|đồng ý|xác nhận|đúng rồi|làm đi|thực hiện)\b)",
    re.I,
)
_NO_RE = re.compile(
    r"(\b(nope|cancel|stop|don't|dont|wait)\b"
    r"|\bno\b(?! thanks to)"
    r"|^\s*(không|khỏi|thôi|huỷ|hủy|dừng|đừng|chờ đã)\b)",
    re.I,
)


def _card_action(session: Session, action: str, text: str) -> FlowResult:
    """Pick a card, confirm, then write the change to the database.

    Every branch that ends in a state change goes through `db.set_card_status`,
    which owns the legal transitions and writes the audit row. This function
    decides *which* card and *whether the customer agreed*; it does not decide
    what a card is allowed to do next.
    """
    spec = CARD_ACTIONS[action]
    customer = session.customer
    eligible = [c for c in customer["cards"] if c["status"] in spec["from"]]

    if not eligible:
        session.reset_flow()
        return FlowResult(text=spec["none_left"], note=f"{action}_none_eligible")

    stage = session.slots.get("card_stage")

    if stage == "confirm":
        if _NO_RE.search(text) and not _YES_RE.search(text):
            session.reset_flow()
            return FlowResult(text=R.CARD_ACTION_CANCELLED, note=f"{action}_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(
                text=f"Quý khách trả lời **có** để {spec['verb']} thẻ, hoặc **không** để giữ nguyên nhé.",
                note=f"{action}_confirm_retry",
            )

        try:
            card = db.set_card_status(
                session.slots["card_id"], action, session_id=session.session_id
            )
        except db.TransitionError as exc:
            # The status changed under us - another session, or the customer
            # doing it in the app while talking to us. Report what the record
            # now says rather than the outcome we expected to produce.
            session.reset_flow()
            return FlowResult(text=str(exc), note=f"{action}_refused")

        session.reset_flow()
        return FlowResult(
            text=_confirmation(action, card), note=f"{action}:{card['card_id']}"
        )

    if stage == "select":
        chosen = _match_card(eligible, text)
        if not chosen:
            return FlowResult(
                text=_card_choice_prompt(eligible, spec["verb"]),
                note=f"{action}_select_retry",
            )
        session.slots["card_id"] = chosen["card_id"]
        session.slots["card_stage"] = "confirm"
        return FlowResult(
            text=spec["confirm"].format(**chosen), note=f"{action}_confirm_prompt"
        )

    session.pending_flow = action
    session.slots["card_action"] = action
    if len(eligible) == 1:
        session.slots["card_id"] = eligible[0]["card_id"]
        session.slots["card_stage"] = "confirm"
        return FlowResult(
            text=spec["confirm"].format(**eligible[0]), note=f"{action}_confirm_prompt"
        )
    session.slots["card_stage"] = "select"
    return FlowResult(
        text=_card_choice_prompt(eligible, spec["verb"]), note=f"{action}_select_prompt"
    )


def _confirmation(action: str, card: dict) -> str:
    """What the customer is told after the record actually changed."""
    reference = card["reference"]
    if action == "freeze":
        return (
            f"Đã tạm khóa **thẻ {card['type']} {card['mask']}** của quý khách "
            f"ngay bây giờ. Mã tham chiếu **{reference}**.\n\n"
            "Mọi giao dịch sẽ bị từ chối, kể cả thanh toán định kỳ. "
            "Quý khách chỉ cần nói **mở khóa thẻ** khi muốn dùng lại."
        )
    if action == "unfreeze":
        return (
            f"**Thẻ {card['type']} {card['mask']}** của quý khách đã được kích hoạt trở lại - "
            f"mã tham chiếu **{reference}**. Quý khách có thể sử dụng ngay bây giờ.\n\n"
            "Nếu có giao dịch nào bị từ chối trong thời gian tạm khóa, "
            "đơn vị thụ hưởng sẽ cần thực hiện lại giao dịch đó."
        )
    replacement = card.get("replacement") or {}
    return (
        f"Đã khóa **thẻ {card['type']} {card['mask']}** của quý khách, "
        f"có hiệu lực ngay lập tức. Mã tham chiếu **{reference}**.\n\n"
        f"Tôi đã đặt thẻ thay thế: **{replacement.get('mask', 'thẻ mới')}**, "
        "sẽ đến trong 5-7 ngày làm việc, miễn phí. Quý khách cần kích hoạt thẻ khi nhận được, "
        "và do số thẻ thay đổi, quý khách vui lòng cập nhật các thanh toán định kỳ liên kết với thẻ cũ.\n\n"
        "Quý khách có muốn hỏi thêm về thẻ cũ không?"
    )


def _card_choice_prompt(cards: list[dict], verb: str) -> str:
    options = "\n".join(
        f"- Thẻ {c['type']} {c['mask']}"
        + ("" if c["status"] == "active" else f" ({c['status']})")
        for c in cards
    )
    return (
        f"Quý khách muốn {verb} thẻ nào ạ?\n{options}\n\n"
        "Quý khách vui lòng trả lời bằng 4 số cuối hoặc loại thẻ."
    )


def _match_card(cards: list[dict], text: str) -> dict | None:
    for card in cards:
        if card["mask"][-4:] in text:
            return card
    for card in cards:
        if card["type"].lower() in text.lower():
            return card
    return None


# -- new flows (product comparison, cross-sell, card close, limit adjust) --

_AMOUNT_RE = re.compile(r"(\d[\d.,]*)\s*(triệu|tr\b|million)?", re.IGNORECASE)


def _parse_amount(text: str) -> float | None:
    match = _AMOUNT_RE.search(text or "")
    if not match:
        return None
    digits = match.group(1).replace(".", "").replace(",", "")
    if not digits.isdigit():
        return None
    value = float(digits)
    if match.group(2):
        value *= 1_000_000
    return value


def _render_match(card: dict, match) -> FlowResult:
    lines = [f"{card['name']} phù hợp với quý khách:", ""]
    for line in match.matched_lines:
        lines.append(f"- {line.text}")
    return FlowResult(text="\n".join(lines), note="cross_sell_matched:" + card["id"])


def _product_comparison(text: str) -> FlowResult:
    mentioned = ENGINE.mentions(text)
    if len(mentioned) < 2:
        return FlowResult(text="", handled=False)
    mentioned = mentioned[:3]
    lines = [f"So sánh {' và '.join(p['name'] for p in mentioned)}:", ""]
    for product in mentioned:
        fee = product.get("annual_fee", {})
        lines.append(f"**{product['name']}**")
        lines.append(f"- {product['value_proposition']}")
        lines.append(
            f"- Phí thường niên: {fee.get('amount', '')} ({fee.get('waiver_condition', '')})"
        )
        lines.append("")
    return FlowResult(text="\n".join(lines).strip(), note="product_comparison")


def _card_catalogue(text: str = "") -> FlowResult:
    """Card enquiries, progressive disclosure:

      * one card named ("thẻ Platinum có gì") -> full detail of that card
      * otherwise ("ngân hàng có những loại thẻ nào") -> a compact one-line
        summary of all four, then invite the customer to dig into one.

    Authoritative from products.json, so it never mis-retrieves a dispute doc or
    lets the LLM answer generically with "thẻ ATM, thẻ tín dụng"."""
    mentioned = ENGINE.mentions(text)
    if len(mentioned) == 1:
        return _card_detail(mentioned[0])

    lines = ["Ngân hàng ABC hiện phát hành các dòng thẻ sau:", ""]
    for product in ENGINE.products:
        # Mệnh đề đầu của value_proposition cho gọn một dòng (tóm tắt).
        vp = (
            (product.get("value_proposition") or "")
            .split(" - ")[0]
            .split(".")[0]
            .strip()
        )
        lines.append(
            f"- **{product['name']}** – {vp}" if vp else f"- **{product['name']}**"
        )
    lines.append("")
    lines.append(
        "Quý khách muốn tìm hiểu kỹ thẻ nào, hay để tôi gợi ý thẻ phù hợp "
        "với chi tiêu của quý khách ạ?"
    )
    return FlowResult(text="\n".join(lines), note="card_catalogue")


def outbound_greeting(title: str, name: str, card_id: str) -> str:
    """Lời chào mở đầu cho cuộc gọi cross-sell mô phỏng (demo). Ghép danh xưng +
    tên khách admin nhập với đúng dòng thẻ được chọn (dữ liệu thật từ
    products.json). Theo văn phong pitch của demo_tutorial.md."""
    card = (
        next((p for p in ENGINE.products if p["id"] == card_id), None)
        or ENGINE.products[0]
    )
    hook = (card.get("welcome_offers") or card.get("reward_scheme") or [""])[0]
    who = (
        " ".join(part for part in (title.strip(), name.strip()) if part) or "quý khách"
    )
    return (
        f"Dạ em chào {who}, em là trợ lý ảo của Ngân hàng ABC ạ. Bên em thấy "
        f"{who} là khách hàng thân thiết nên đã ưu tiên duyệt trước **{card['name']}** "
        f"cho mình. Thẻ đang có ưu đãi: {hook}. Không biết {who} có muốn em tư vấn "
        "nhanh để nhận thẻ luôn không ạ?"
    )


def _card_detail(card: dict) -> FlowResult:
    """The full 'table row' for one card - ưu đãi chào mừng, quyền lợi/hoàn tiền,
    phí thường niên & miễn phí năm 2 - all straight from products.json. This is
    the 'khách hỏi thêm thì giới thiệu thêm' step after the compact catalogue."""
    lines = [f"**{card['name']}** – {card.get('value_proposition', '')}", ""]
    welcome = card.get("welcome_offers") or []
    if welcome:
        lines.append("**Ưu đãi chào mừng:**")
        lines += [f"- {w}" for w in welcome]
        lines.append("")
    rewards = card.get("reward_scheme") or []
    if rewards:
        lines.append("**Quyền lợi & hoàn tiền:**")
        lines += [f"- {r}" for r in rewards]
        lines.append("")
    fee = card.get("annual_fee") or {}
    if fee.get("amount"):
        cond = fee.get("waiver_condition", "")
        lines.append(
            f"**Phí thường niên:** {fee['amount']}" + (f" — {cond}" if cond else "")
        )
        lines.append("")
    lines.append("Quý khách muốn mở thẻ này, hay so sánh với dòng thẻ khác ạ?")
    return FlowResult(text="\n".join(lines).strip(), note="card_detail:" + card["id"])


def _card_application(session: Session) -> FlowResult:
    """TH1: thủ tục/điều kiện mở thẻ. 'Kết hợp': thủ tục chung cho mọi khách, và
    nếu khách ĐÃ xác minh + nằm trong danh sách target cross-sell thì thêm dòng
    'đã được duyệt trước' (đúng ngữ cảnh demo_tutorial, không nói bừa với khách
    vãng lai)."""
    preapproved = ""
    if session.verified and session.customer_id:
        offers = [
            o
            for o in CAMPAIGNS.offers_for(session.customer_id)
            if o.type == "cross_sell"
        ]
        if offers:
            preapproved = (
                f"Tin vui: quý khách nằm trong danh sách **được duyệt trước** cho "
                f"{offers[0].name} — không cần bổ sung giấy tờ, chỉ cần xác nhận là "
                "mở được ngay ạ.\n\n"
            )
    body = (
        "Thủ tục mở thẻ tín dụng thông thường:\n\n"
        "- **Giấy tờ:** CMND/CCCD còn hiệu lực.\n"
        "- **Chứng minh thu nhập:** tùy hạng thẻ (hạng cao yêu cầu cao hơn); có thể "
        "được miễn nếu quý khách đã có lịch sử giao dịch với ngân hàng.\n"
        "- **Cách đăng ký:** trực tuyến qua ứng dụng/website, hoặc tại chi nhánh.\n\n"
        "Quý khách muốn tôi tư vấn thẻ phù hợp, hay hướng dẫn các bước mở thẻ ạ?"
    )
    return FlowResult(text=preapproved + body, note="card_application")


def _cross_sell_interest(session: Session, text: str) -> FlowResult:
    # "tôi không muốn mở thẻ" phân loại vào đây vì "mở thẻ" lấn át phần phủ định.
    # Đó là lời từ chối - rút lui lịch sự, không chào thẻ (TH3).
    if _NO_INTEREST_RE.search(text):
        session.reset_flow()
        return FlowResult(text=R.DECLINE_GRACEFUL, note="decline")

    # TH1: câu hỏi thủ tục/điều kiện mở thẻ -> trả lời thủ tục, không hỏi chi tiêu.
    if _APPLICATION_RE.search(text):
        session.reset_flow()
        return _card_application(session)

    # Two modes depending on whether identity has been verified:
    #
    # Verified  → segment-locked: the bank already assigned a card tier to this
    #             customer; explain why that specific card fits what they said.
    # Unverified → catalogue search: no segment known, so score the stated
    #             interest against all four products and recommend the best fit.
    #             (Scenario 1 – Customer Service: customer explores without login.)
    already_asked = session.slots.get("cross_sell_asked") == "1"

    if session.verified and session.customer:
        profile = session.customer
        card = ENGINE.card_for_segment(profile.get("segment"))
        if not card:
            return FlowResult(
                text="Tôi chưa xác định được thẻ phù hợp với hồ sơ của quý khách.",
                note="cross_sell_no_card",
            )
        interest_text = text.strip() or " ".join(profile.get("stated_interests") or [])
        if interest_text:
            match = ENGINE.explain_fit(card, interest_text)
            specific = any(
                line.source != "value_proposition" for line in match.matched_lines
            )
            if (
                specific
                or already_asked
                or profile.get("stated_interests")
                or interest_text
            ):
                session.reset_flow()
                return _render_match(card, match)
        session.pending_flow = "cross_sell_interest"
        session.slots["cross_sell_asked"] = "1"
        return FlowResult(
            text=(
                f"{_first_name(profile)} hay mua hàng trên nền tảng nào, "
                "hay thường chi tiêu nhiều ở đâu nhất?"
            ),
            note="cross_sell_ask_interest_verified",
        )

    # Unverified path — consultative, not pushy. On the FIRST message we do not
    # pitch a card: we acknowledge and ask one discovery question to understand
    # the need, keeping whatever interest was already named. On the SECOND we
    # recommend, combining both messages. already_asked closes the loop, so the
    # discovery question is asked exactly once and the turn always resolves.
    interest_text = text.strip()

    if not already_asked:
        if _STATED_INTEREST_RE.search(interest_text):
            session.slots["xsell_interest"] = interest_text
        session.pending_flow = "cross_sell_interest"
        session.slots["cross_sell_asked"] = "1"
        return FlowResult(text=R.CROSS_SELL_DISCOVER, note="cross_sell_discover")

    combined = " ".join(
        part for part in (session.slots.get("xsell_interest"), interest_text) if part
    ).strip()
    best = ENGINE.recommend_all(combined)
    session.reset_flow()
    if best is not None:
        return _render_match(best["card"], best["match"])
    # Even after discovery, nothing an openly-marketed card specifically covers
    # (e.g. golf, du lịch nước ngoài). Show the open catalogue rather than loop.
    return FlowResult(text=R.CROSS_SELL_NO_OPEN_MATCH, note="cross_sell_no_open_match")


def _reward_inquiry(session: Session) -> FlowResult:
    profile = session.customer
    card = ENGINE.card_for_segment(profile.get("segment"))
    if not card:
        return FlowResult(
            text=f"Tôi chưa xác định được thẻ phù hợp với hồ sơ của {_first_name(profile)}.",
            note="reward_inquiry_no_card",
        )
    lines = [f"Quyền lợi hiện có trên {card['name']}:", ""]
    for line in card.get("reward_scheme", []):
        lines.append(f"- {line}")
    return FlowResult(text="\n".join(lines), note="reward_inquiry:" + card["id"])


def _card_close(session: Session, text: str) -> FlowResult:
    stage = session.slots.get("card_close_stage")
    if stage == "await_confirm":
        session.reset_flow()
        if _NO_RE.search(text):
            return FlowResult(text=R.CARD_CLOSE_CANCELLED, note="card_close_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(text=R.CARD_CLOSE_UNCLEAR, note="card_close_unclear")
        try:
            result = cards.close_card(session.customer_id, session.session_id)
        except cards.TransitionError as exc:
            return FlowResult(text=str(exc), note="card_close_error")
        return FlowResult(
            text=f"Đã đóng thẻ của quý khách (mã tham chiếu {result['reference']}).",
            note="card_close_done",
        )
    card = cards.get_card(session.customer_id)
    if not card:
        return FlowResult(text=R.CARD_CLOSE_NONE, note="card_close_none")
    if card["status"] == "closed":
        return FlowResult(
            text=R.CARD_CLOSE_ALREADY_CLOSED, note="card_close_already_closed"
        )
    if card["status"] != "active":
        return FlowResult(text=R.CARD_CLOSE_NONE, note="card_close_none")
    session.pending_flow = "card_close"
    session.slots["card_close_stage"] = "await_confirm"
    return FlowResult(text=R.CARD_CLOSE_CONFIRM_ASK, note="card_close_confirm")


def _submit_limit_request(session: Session, amount: float | None) -> FlowResult:
    if amount is None:
        return FlowResult(text=R.LIMIT_INVALID, note="card_limit_invalid")
    try:
        result = cards.request_limit_adjustment(
            session.customer_id, amount, session.session_id
        )
    except cards.TransitionError as exc:
        return FlowResult(text=str(exc), note="card_limit_error")
    return FlowResult(
        text=(
            f"Tôi đã ghi nhận yêu cầu điều chỉnh hạn mức lên {_money(amount)} "
            f"(mã tham chiếu {result['reference']}) rồi nhé. Đây là yêu cầu chờ duyệt - "
            "hạn mức hiện tại chưa thay đổi cho tới khi có kết quả xét duyệt."
        ),
        note="card_limit_requested",
    )


def _card_limit_adjust(session: Session, text: str) -> FlowResult:
    stage = session.slots.get("limit_stage")
    if stage == "await_amount":
        amount = _parse_amount(text)
        session.reset_flow()
        return _submit_limit_request(session, amount)
    amount = _parse_amount(text)
    if amount is not None:
        return _submit_limit_request(session, amount)
    session.pending_flow = "card_limit_adjust"
    session.slots["limit_stage"] = "await_amount"
    return FlowResult(text=R.LIMIT_ASK_AMOUNT, note="card_limit_ask_amount")


# -- dispatch -------------------------------------------------------------


def handle(session: Session, intent: str, text: str) -> FlowResult:
    """Run the scripted flow for `intent`, gating protected ones on verification."""
    if intent in PROTECTED_FLOWS and not session.verified:
        return _verification_prompt(session, intent)

    if intent == "account_summary":
        return _account_summary(session)
    if intent == "balance_inquiry":
        return _balance(session)
    if intent == "transaction_history":
        return _transactions(session)
    if intent == "loan_status":
        return _loan_status(session)
    if intent == "block_card":
        return _card_action(session, "report_lost", text)
    if intent == "freeze_card":
        return _card_action(session, "freeze", text)
    if intent == "unfreeze_card":
        return _card_action(session, "unfreeze", text)
    if intent == "product_comparison":
        return _product_comparison(text)
    if intent == "card_catalogue":
        return _card_catalogue(text)
    if intent == "cross_sell_interest":
        return _cross_sell_interest(session, text)
    if intent == "reward_inquiry":
        return _reward_inquiry(session)
    if intent == "card_close":
        return _card_close(session, text)
    if intent == "card_limit_adjust":
        return _card_limit_adjust(session, text)
    if intent == "greeting":
        return FlowResult(text=R.GREETING, note="greeting")
    if intent == "activate_card":
        return _activate_card(session)
    if intent == "card_offers":
        return _card_offers(session)
    if intent == "smalltalk":
        # Acknowledgements and "are you there?" are not questions. Sending them
        # through retrieval produced the worst turn in the demo: a customer
        # typing "ok thanks" was offered a human agent.
        return FlowResult(text=R.SMALLTALK, note="smalltalk")
    if intent == "goodbye":
        return FlowResult(text=R.GOODBYE, note="goodbye")
    if intent == "decline":
        # TH3: khách từ chối/bận - lịch sự rút lui, không nài, không đẩy chuyên viên.
        return FlowResult(text=R.DECLINE_GRACEFUL, note="decline")
    if intent == "human_agent":
        return FlowResult(
            text=R.HUMAN_AGENT_HANDOFF,
            escalate=True,
            escalation_reason="Customer explicitly asked for a human agent",
            note="explicit_handoff_request",
        )

    return FlowResult(text="", handled=False)


CANCEL_RE = re.compile(
    r"\b(cancel|stop|never ?mind|nevermind|forget it|no thanks|"
    r"skip|quit|exit|go back|start over|something else)\b",
    re.IGNORECASE,
)

# A flow that re-prompts this many times in a row is not going to succeed. The
# customer is answering a different question than the one being asked.
MAX_FLOW_MISSES = 2


def wants_out(text: str) -> bool:
    return bool(CANCEL_RE.search(text))


def continue_pending(session: Session, text: str) -> FlowResult | None:
    """Resume a mid-conversation flow before the classifier gets a say.

    Slot answers like "9411 3147" or "yes" carry no intent signal, so an in-progress
    flow must take priority over classification.
    """
    if session.pending_flow == "verify":
        return _handle_verification(session, text)
    # Resumed by the action stored when the flow began, so freeze, unfreeze and
    # report-lost each come back to their own branch rather than all landing in
    # whichever one happened to be hard-coded here.
    if session.pending_flow in CARD_ACTIONS:
        return _card_action(session, session.pending_flow, text)
    # Card close and limit adjust each have their own confirmation step.
    # Without these, a "có" reply after the confirmation prompt falls through
    # to NLU which cannot classify it correctly.
    if session.pending_flow == "card_close":
        return _card_close(session, text)
    if session.pending_flow == "card_limit_adjust":
        return _card_limit_adjust(session, text)
    # Cross-sell interest: the bot asked about spending preference and needs the
    # customer's reply even when NLU cannot classify a bare "mua sắm online".
    if session.pending_flow == "cross_sell_interest":
        return _cross_sell_interest(session, text)
    return None
