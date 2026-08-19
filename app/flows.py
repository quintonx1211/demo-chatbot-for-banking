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

from . import cards, memory
from .campaigns import CampaignBook
from . import db
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
    """Start (or continue) the identity check before a protected flow runs.

    Two factors, asked for and checked together: the phone number alone was a
    4-digit space with no lockout wider than one session - a customer (or an
    attacker) could open a fresh session after every third guess and keep
    dialling. A second, independent factor - the national ID (ID) - does
    not fix that on its own, but it takes the search space from "guess one
    4-digit code" to "guess two 4-digit codes for the same person at once",
    which is the cheapest real improvement available without a true step-up
    channel (OTP to the registered device) standing in for a demo.
    """
    session.pending_flow = "verify"
    session.slots["target_intent"] = intent
    return FlowResult(
        text=(
            "Tôi sẵn sàng hỗ trợ. Trước tiên, cần xác minh nhanh để đảm bảo đây là bạn.\n\n"
            "Bạn có thể gửi **4 số cuối số điện thoại đã đăng ký** và "
            "**4 số cuối CMND/CCCD**, cách nhau bằng dấu cách không?"
        ),
        note="verification_started",
    )


def _handle_verification(session: Session, text: str) -> FlowResult:
    # The message has to *be* the two codes, not merely contain them.
    #
    # Scraping the first two 4-digit groups out of free text let a customer who
    # pasted a card number verify themselves on its opening digits - the part
    # of a card that is printed on statements and shared with every merchant,
    # and identical for everyone holding the same product. Anything that is not
    # exactly two groups is far more likely to be a customer typing a sentence,
    # or pasting something they should not, than an identity check.
    digits = re.findall(r"\d{4}", re.sub(r"[^0-9]+", " ", text))
    if len(digits) != 2 or re.search(r"\d{5,}", re.sub(r"[^0-9]+", " ", text)):
        return FlowResult(
            text=("Chưa đúng - tôi cần hai mã gồm 4 chữ số: "
                  "4 số cuối số điện thoại, rồi 4 số cuối CMND/CCCD. "
                  "Ví dụ: `1234 5678`.\n\n"
                  "Vui lòng không gửi số thẻ đầy đủ hoặc mã PIN - tôi không bao giờ cần những thông tin đó."),
            note="verification_retry",
        )

    phone, national_id = digits[0], digits[1]
    # Both factors have to match the same customer. The failure message below
    # does not say which one was wrong - telling an attacker "the phone
    # matched but the ID didn't" turns two independent secrets into one,
    # guessed a factor at a time.
    match = db.find_by_credentials(phone, national_id)
    if not match:
        attempts = int(session.slots.get("verify_attempts", "0")) + 1
        session.slots["verify_attempts"] = str(attempts)
        if attempts >= 3:
            session.reset_flow()
            return FlowResult(
                text=("Xin lỗi - tôi không thể xác minh thông tin của bạn, "
                      "và không thể tiếp tục thử thêm. Để tôi chuyển bạn đến "
                      "chuyên viên để hỗ trợ bạn trực tiếp."),
                escalate=True,
                escalation_reason="Xác minh danh tính thất bại ba lần",
                note="verification_failed",
            )
        return FlowResult(
            text=(f"Thông tin không khớp với hồ sơ của chúng tôi (lần thử {attempts}/3). "
                  "Vui lòng thử lại với cả hai mã."),
            note="verification_retry",
        )

    session.customer_id = match["customer_id"]
    session.verified = True
    target = session.slots.get("target_intent")
    session.reset_flow()

    # Cross-session recall lands here and nowhere earlier: before this line the
    # session has no verified customer, so there is nobody to remember.
    recalled = memory.store.summary(match["customer_id"])
    greeting = f"Cảm ơn, {_first_name(match)} - bạn đã được xác minh. "
    if recalled:
        greeting += recalled + " "
    if target:
        follow_up = handle(session, target, "")
        return FlowResult(text=greeting + follow_up.text,
                          escalate=follow_up.escalate,
                          escalation_reason=follow_up.escalation_reason,
                          note="verified_then_" + target)
    return FlowResult(text=greeting + "Tôi có thể giúp gì cho bạn?", note="verified")


# -- account flows --------------------------------------------------------

def _balance(session: Session) -> FlowResult:
    customer = session.customer
    lines = [f"Đây là số dư tài khoản của bạn tính đến hôm nay, {_first_name(customer)}:"]
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
        f"Bạn là **{customer['name']}** ({customer['customer_id']}), "
        f"đã được xác minh trong cuộc hội thoại này.",
        "",
        "Đây là các sản phẩm bạn đang có với chúng tôi:",
    ]
    for account in customer["accounts"]:
        lines.append(f"- **{account['type']}** {account['mask']}")
    for card in customer["cards"]:
        state = "" if card["status"] == "active" else f" - {card['status']}"
        lines.append(f"- **Thẻ {card['type']}** {card['mask']}{state}")
    for loan in customer.get("loans", []):
        lines.append(f"- **{loan['product']}** hồ sơ {loan['application_id']} "
                     f"- {loan['status']}")
    lines.append("")
    lines.append("Tôi có thể xem chi tiết về bất kỳ mục nào ở trên cho bạn.")
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
                f"Thẻ **{card['type']} {card['mask']}** của bạn đang ở trạng thái ngủ đông do "
                f"không có giao dịch trong thời gian dài.{offer_line}\n\n"
                f"**{dormant_campaign.get('cta', 'Thực hiện một giao dịch để kích hoạt lại')}** - "
                f"{dormant_campaign.get('deeplink', '')}\n\n"
                "_Không cần liên hệ chi nhánh - chỉ cần dùng thẻ là thẻ tự động hoạt động trở lại._"
            ),
            note=f"dormant_reactivation:{card['card_id']}",
        )

    if not inactive:
        return FlowResult(
            text=("Tất cả thẻ trong hồ sơ của bạn đã được kích hoạt. Nếu một "
                  "giao dịch bị từ chối, đó là vấn đề khác và tôi có thể kiểm tra cho bạn."),
            note="activation_none_pending",
        )

    card = inactive[0]
    return FlowResult(
        text=(
            f"Thẻ **{card['type']} {card['mask']}** của bạn đã được cấp nhưng chưa kích hoạt.\n\n"
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
        return FlowResult(
            text=("Bạn hiện không có ưu đãi nào, vậy nên tôi không có gì để "
                  "hiển thị hôm nay. Bạn cần hỏi thêm điều gì không?"),
            note="offers_none",
        )

    lines = ["Đây là các ưu đãi hiện có trên tài khoản của bạn hôm nay:", ""]
    for offer in offers:
        lines.append(f"**{offer.name}**")
        lines.append(offer.body)
        if offer.deeplink:
            lines.append(f"→ {offer.cta}: {offer.deeplink}")
        lines.append("")
    lines.append(
        f"_Được hệ thống chiến dịch của ngân hàng chọn, cập nhật lần cuối "
        f"{CAMPAIGNS.age_hours:.0f} giờ trước._" if CAMPAIGNS.age_hours is not None
        else ""
    )
    return FlowResult(
        text="\n".join(l for l in lines if l is not None).strip(),
        note="offers:" + ",".join(o.campaign_id for o in offers),
    )


def proactive_offer(session: Session) -> FlowResult | None:
    """An offer worth raising unprompted, once identity is established.

    Only service-blocking situations qualify - a card that will not work
    because it was never activated, or one that has gone dormant. Cross-sell is
    never volunteered: a customer who came to ask about a fee has not asked to
    be sold to, and interrupting them with an upgrade is how these systems get
    switched off.
    """
    if session.slots.get("campaign_offered"):
        return None
    offers = [o for o in CAMPAIGNS.offers_for(session.customer_id)
              if o.type in ("activation", "reactivation")]
    if not offers:
        return None

    session.slots["campaign_offered"] = "1"
    offer = offers[0]
    return FlowResult(
        text=(f"\n\n---\n**Nhân tiện:** {offer.body}\n\n"
              f"→ {offer.cta}: {offer.deeplink}"),
        note=f"proactive:{offer.campaign_id}",
    )


def _transactions(session: Session) -> FlowResult:
    customer = session.customer
    transactions = customer["transactions"][:5]
    if not transactions:
        return FlowResult(text="Tôi không thấy giao dịch gần đây nào trên tài khoản của bạn.")
    lines = ["Các giao dịch gần nhất của bạn:"]
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
        return FlowResult(
            text=("Tôi không thấy hồ sơ vay nào đang mở trong tài khoản của bạn. "
                  "Nếu bạn nộp hồ sơ tại chi nhánh trong vòng 24 giờ qua, có thể chưa đồng bộ - "
                  "tôi có thể chuyển bạn đến bộ phận tín dụng để kiểm tra."),
            note="no_loan_on_file",
        )
    lines = ["Đây là trạng thái hồ sơ vay của bạn:"]
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
        "confirm": ("Xác nhận lại: báo mất và khóa **thẻ {type} {mask}** của bạn?\n\n"
                    "Thao tác này không thể hoàn tác - thẻ đã khóa sẽ không mở lại được, "
                    "vì vậy tôi sẽ đặt thẻ thay thế cho bạn cùng lúc. Nếu bạn chỉ thất lạc "
                    "tạm thời và nghĩ sẽ tìm lại được, hãy nói **tạm khóa** để có thể mở khóa sau."),
        "none_left": "Hiện bạn không có thẻ nào để khóa.",
    },
    "freeze": {
        "verb": "tạm khóa",
        "from": ("active",),
        "reversible": True,
        "confirm": ("Tôi có thể tạm khóa **thẻ {type} {mask}** của bạn ngay. "
                    "Mọi giao dịch sẽ bị từ chối cho đến khi bạn mở khóa, "
                    "và bạn có thể làm điều đó bất cứ lúc nào tại đây.\n\nTrả lời **có** để tạm khóa."),
        "none_left": "Bạn không có thẻ đang hoạt động nào để tạm khóa.",
    },
    "unfreeze": {
        "verb": "mở khóa",
        "from": ("frozen",),
        "reversible": True,
        "confirm": ("Sẵn sàng mở khóa **thẻ {type} {mask}** của bạn - thẻ sẽ hoạt động "
                    "ngay lập tức.\n\nTrả lời **có** để xác nhận."),
        "none_left": ("Hiện không có thẻ nào đang bị tạm khóa. Nếu thẻ đã bị báo mất "
                      "thì đang ở trạng thái khóa vĩnh viễn, không thể mở lại - "
                      "nhưng tôi có thể kiểm tra thẻ thay thế cho bạn."),
    },
}

# Confirmation words in both languages. The Vietnamese alternatives are
# anchored to the start of the message while the English ones are not, and that
# asymmetry is deliberate: "có" and "không" are extremely common syllables
# inside ordinary Vietnamese sentences ("tôi không tìm thấy thẻ", "thẻ này có
# vấn đề"), so an unanchored match would read a confirmation out of a sentence
# that was not answering the question. English "yes"/"no" as whole words are
# far less ambiguous mid-sentence.
_YES_RE = re.compile(
    r"(\b(yes|yeah|yep|confirm|correct|do it|go ahead)\b"
    r"|^\s*(có|dạ|vâng|ừ|ok|đồng ý|xác nhận|đúng rồi|làm đi|thực hiện)\b)", re.I)
_NO_RE = re.compile(
    r"(\b(nope|cancel|stop|don't|dont|wait)\b"
    r"|\bno\b(?! thanks to)"
    r"|^\s*(không|khỏi|thôi|huỷ|hủy|dừng|đừng|chờ đã)\b)", re.I)


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
            return FlowResult(text="Được rồi - tôi đã giữ nguyên trạng thái thẻ.",
                              note=f"{action}_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(
                text=f"Trả lời **có** để {spec['verb']} thẻ, hoặc **không** để giữ nguyên.",
                note=f"{action}_confirm_retry")

        try:
            card = db.set_card_status(session.slots["card_id"], action,
                                      session_id=session.session_id)
        except db.TransitionError as exc:
            # The status changed under us - another session, or the customer
            # doing it in the app while talking to us. Report what the record
            # now says rather than the outcome we expected to produce.
            session.reset_flow()
            return FlowResult(text=str(exc), note=f"{action}_refused")

        session.reset_flow()
        return FlowResult(text=_confirmation(action, card), note=f"{action}:{card['card_id']}")

    if stage == "select":
        chosen = _match_card(eligible, text)
        if not chosen:
            return FlowResult(text=_card_choice_prompt(eligible, spec["verb"]),
                              note=f"{action}_select_retry")
        session.slots["card_id"] = chosen["card_id"]
        session.slots["card_stage"] = "confirm"
        return FlowResult(text=spec["confirm"].format(**chosen),
                          note=f"{action}_confirm_prompt")

    session.pending_flow = action
    session.slots["card_action"] = action
    if len(eligible) == 1:
        session.slots["card_id"] = eligible[0]["card_id"]
        session.slots["card_stage"] = "confirm"
        return FlowResult(text=spec["confirm"].format(**eligible[0]),
                          note=f"{action}_confirm_prompt")
    session.slots["card_stage"] = "select"
    return FlowResult(text=_card_choice_prompt(eligible, spec["verb"]),
                      note=f"{action}_select_prompt")


def _confirmation(action: str, card: dict) -> str:
    """What the customer is told after the record actually changed."""
    reference = card["reference"]
    if action == "freeze":
        return (f"Xong - **thẻ {card['type']} {card['mask']}** của bạn đã được tạm khóa "
                f"ngay bây giờ. Mã tham chiếu **{reference}**.\n\n"
                "Mọi giao dịch sẽ bị từ chối, kể cả thanh toán định kỳ. "
                "Chỉ cần nói **mở khóa thẻ** khi bạn muốn dùng lại.")
    if action == "unfreeze":
        return (f"**Thẻ {card['type']} {card['mask']}** của bạn đã được kích hoạt trở lại - "
                f"mã tham chiếu **{reference}**. Bạn có thể sử dụng ngay bây giờ.\n\n"
                "Nếu có giao dịch nào bị từ chối trong thời gian tạm khóa, "
                "đơn vị thụ hưởng sẽ cần thực hiện lại giao dịch đó.")
    replacement = card.get("replacement") or {}
    return (f"Xong - **thẻ {card['type']} {card['mask']}** của bạn đã bị khóa "
            f"có hiệu lực ngay lập tức. Mã tham chiếu **{reference}**.\n\n"
            f"Tôi đã đặt thẻ thay thế: **{replacement.get('mask', 'thẻ mới')}**, "
            "sẽ đến trong 5-7 ngày làm việc, miễn phí. Bạn cần kích hoạt thẻ khi nhận được, "
            "và do số thẻ thay đổi, hãy cập nhật các thanh toán định kỳ liên kết với thẻ cũ.\n\n"
            "Bạn có muốn hỏi thêm về thẻ cũ không?")


def _card_choice_prompt(cards: list[dict], verb: str) -> str:
    options = "\n".join(
        f"- Thẻ {c['type']} {c['mask']}"
        + ("" if c["status"] == "active" else f" ({c['status']})")
        for c in cards)
    return (f"Bạn muốn tôi {verb} thẻ nào?\n{options}\n\n"
            "Trả lời bằng 4 số cuối hoặc loại thẻ.")


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
    lines = [f"{card['name']} phù hợp với điều bạn vừa nói:", ""]
    for line in match.matched_lines:
        lines.append(f"- {line.text}")
    lines.append("")
    lines.append(f"_{match.disclaimer}_")
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
        lines.append(f"- Phí thường niên: {fee.get('amount', '')} ({fee.get('waiver_condition', '')})")
        lines.append("")
    return FlowResult(text="\n".join(lines).strip(), note="product_comparison")


def _cross_sell_interest(session: Session, text: str) -> FlowResult:
    profile = session.customer
    card = ENGINE.card_for_segment(profile.get("segment"))
    if not card:
        return FlowResult(
            text=f"Tôi chưa xác định được thẻ phù hợp với hồ sơ của {_first_name(profile)}.",
            note="cross_sell_no_card",
        )
    already_asked = session.slots.get("cross_sell_asked") == "1"
    interest_text = text.strip() or " ".join(profile.get("stated_interests") or [])
    if interest_text:
        match = ENGINE.explain_fit(card, interest_text)
        specific = any(line.source != "value_proposition" for line in match.matched_lines)
        if specific or already_asked or profile.get("stated_interests"):
            session.reset_flow()
            return _render_match(card, match)
    session.pending_flow = "cross_sell_interest"
    session.slots["cross_sell_asked"] = "1"
    return FlowResult(
        text=(f"{_first_name(profile)} quan tâm nhất điều gì: mua sắm online, "
              "ăn uống, du lịch, hay chơi golf?"),
        note="cross_sell_ask_interest",
    )


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
    lines.append("")
    lines.append(f"_{ENGINE._disclaimer_for(card)}_")
    return FlowResult(text="\n".join(lines), note="reward_inquiry:" + card["id"])


def _card_close(session: Session, text: str) -> FlowResult:
    stage = session.slots.get("card_close_stage")
    if stage == "await_confirm":
        session.reset_flow()
        if _NO_RE.search(text):
            return FlowResult(text="Đã huỷ yêu cầu - thẻ của bạn vẫn hoạt động bình thường.",
                              note="card_close_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(text="Bạn xác nhận đóng thẻ không? Vui lòng trả lời có hoặc không.",
                              note="card_close_unclear")
        try:
            result = cards.close_card(session.customer_id, session.session_id)
        except cards.TransitionError as exc:
            return FlowResult(text=str(exc), note="card_close_error")
        return FlowResult(text=f"Đã đóng thẻ của bạn (mã tham chiếu {result['reference']}).",
                          note="card_close_done")
    card = cards.get_card(session.customer_id)
    if not card or card["status"] != "active":
        return FlowResult(text="Tôi không tìm thấy thẻ đang hoạt động nào trên hồ sơ của bạn.",
                          note="card_close_none")
    session.pending_flow = "card_close"
    session.slots["card_close_stage"] = "await_confirm"
    return FlowResult(text="Bạn có chắc muốn đóng thẻ này không? Vui lòng xác nhận có/không.",
                      note="card_close_confirm")


def _submit_limit_request(session: Session, amount: float | None) -> FlowResult:
    if amount is None:
        return FlowResult(text="Tôi chưa nhận được số hạn mức hợp lệ, bạn vui lòng thử lại.",
                          note="card_limit_invalid")
    try:
        result = cards.request_limit_adjustment(session.customer_id, amount, session.session_id)
    except cards.TransitionError as exc:
        return FlowResult(text=str(exc), note="card_limit_error")
    return FlowResult(
        text=(f"Đã ghi nhận yêu cầu điều chỉnh hạn mức lên {_money(amount)} "
              f"(mã tham chiếu {result['reference']}). Đây là yêu cầu chờ duyệt - "
              "hạn mức hiện tại chưa thay đổi cho tới khi có kết quả xét duyệt."),
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
    return FlowResult(text="Bạn muốn hạn mức mới là bao nhiêu?", note="card_limit_ask_amount")


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
    if intent == "cross_sell_interest":
        return _cross_sell_interest(session, text)
    if intent == "reward_inquiry":
        return _reward_inquiry(session)
    if intent == "card_close":
        return _card_close(session, text)
    if intent == "card_limit_adjust":
        return _card_limit_adjust(session, text)
    if intent == "greeting":
        return FlowResult(
            text=("Xin chào! Tôi là trợ lý ảo của Ngân hàng ABC. Tôi có thể "
                  "kiểm tra số dư và giao dịch, khóa thẻ mất cắp, tra cứu hồ sơ vay, "
                  "so sánh sản phẩm, xem ưu đãi thẻ, hoặc hỗ trợ đóng thẻ và điều chỉnh hạn mức. "
                  "Bạn cần hỗ trợ gì?"),
            note="greeting",
        )
    if intent == "activate_card":
        return _activate_card(session)
    if intent == "card_offers":
        return _card_offers(session)
    if intent == "smalltalk":
        # Acknowledgements and "are you there?" are not questions. Sending them
        # through retrieval produced the worst turn in the demo: a customer
        # typing "ok thanks" was offered a human agent.
        return FlowResult(
            text="Tôi đây. Bạn cần hỏi thêm điều gì không?",
            note="smalltalk",
        )
    if intent == "goodbye":
        return FlowResult(
            text="Rất vui được hỗ trợ bạn. Chúc bạn một ngày tốt lành!", note="goodbye")
    if intent == "human_agent":
        return FlowResult(
            text="Được, để tôi kết nối bạn ngay.",
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
    return None
