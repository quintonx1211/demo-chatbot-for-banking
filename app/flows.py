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

from . import memory
from .campaigns import CampaignBook
from . import db
from .session import Session

CAMPAIGNS = CampaignBook()

# Flows that must not run for an unverified caller.
PROTECTED_FLOWS = {"balance_inquiry", "block_card", "loan_status",
                   "transaction_history", "account_summary",
                   "activate_card", "card_offers",
                   "freeze_card", "unfreeze_card"}


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
            "Vâng, tôi hỗ trợ anh/chị ngay. Trước tiên xin phép xác minh nhanh "
            "để chắc chắn đúng là chủ tài khoản ạ.\n\n"
            "Anh/chị vui lòng cho tôi **4 số cuối của số điện thoại đã đăng ký** "
            "và **4 số cuối CCCD**, cách nhau một dấu cách."
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
            text=("Chưa đúng định dạng ạ. Tôi chỉ cần đúng hai nhóm 4 số: 4 số "
                  "cuối điện thoại, rồi 4 số cuối CCCD, ví dụ `1234 5678`.\n\n"
                  "Anh/chị lưu ý không gửi số thẻ đầy đủ hay mã PIN - tôi không "
                  "bao giờ cần những thông tin đó."),
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
                text=("Tôi rất tiếc, thông tin xác minh vẫn chưa khớp và tôi "
                      "không thể thử thêm ở đây. Tôi xin chuyển anh/chị sang "
                      "chuyên viên để được hỗ trợ trực tiếp ạ."),
                escalate=True,
                escalation_reason="Identity verification failed three times",
                note="verification_failed",
            )
        return FlowResult(
            text=(f"Thông tin chưa khớp với hồ sơ của chúng tôi (lần {attempts}/3). "
                  "Anh/chị vui lòng nhập lại cả hai mã giúp tôi ạ."),
            note="verification_retry",
        )

    session.customer_id = match["customer_id"]
    session.verified = True
    target = session.slots.get("target_intent")
    session.reset_flow()

    # Cross-session recall lands here and nowhere earlier: before this line the
    # session has no verified customer, so there is nobody to remember.
    recalled = memory.store.summary(match["customer_id"])
    greeting = f"Cảm ơn anh/chị {_first_name(match)}, đã xác thực thành công. "
    if recalled:
        greeting += recalled + " "
    if target:
        follow_up = handle(session, target, "")
        return FlowResult(text=greeting + follow_up.text,
                          escalate=follow_up.escalate,
                          escalation_reason=follow_up.escalation_reason,
                          note="verified_then_" + target)
    return FlowResult(text=greeting + "Tôi có thể hỗ trợ gì cho anh/chị ạ?",
                      note="verified")


# -- account flows --------------------------------------------------------

def _balance(session: Session) -> FlowResult:
    customer = session.customer
    lines = [f"Số dư tài khoản của anh/chị {_first_name(customer)} tính đến "
             f"hôm nay:"]
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
        f"Anh/chị là **{customer['name']}** ({customer['customer_id']}), "
        f"đã xác thực trong phiên này.",
        "",
        "Các sản phẩm anh/chị đang có tại ngân hàng:",
    ]
    for account in customer["accounts"]:
        lines.append(f"- **{account['type']}** {account['mask']}")
    for card in customer["cards"]:
        state = "" if card["status"] == "active" else f" - {card['status']}"
        lines.append(f"- **Thẻ {card['type']}** {card['mask']}{state}")
    for loan in customer.get("loans", []):
        lines.append(f"- **{loan['product']}** - hồ sơ {loan['application_id']} "
                     f"- {loan['status']}")
    lines.append("")
    lines.append("Anh/chị muốn xem chi tiết mục nào, tôi tra cứu giúp ạ.")
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
    campaign = CAMPAIGNS.campaigns.get("CARD-ACTIVATION", {})

    if not inactive:
        return FlowResult(
            text=("Tất cả thẻ trên hồ sơ của anh/chị đều đã kích hoạt rồi ạ. "
                  "Nếu có giao dịch bị từ chối thì đó là vấn đề khác, tôi kiểm "
                  "tra giúp anh/chị được."),
            note="activation_none_pending",
        )

    card = inactive[0]
    return FlowResult(
        text=(
            f"**Thẻ {card['type']} {card['mask']}** của anh/chị đã được phát "
            f"hành nhưng chưa kích hoạt.\n\n"
            f"**{campaign.get('cta', 'Kích hoạt trong ứng dụng')}** - "
            f"{campaign.get('deeplink', '')}\n\n"
            f"Hoặc: {campaign.get('sms_alternative', 'gọi số in trên thẻ')}.\n\n"
            "_Tôi sẽ không bao giờ hỏi mã OTP trong khung chat này. Nếu có ai "
            "tự xưng là ngân hàng mà hỏi, đó không phải chúng tôi._"
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
            text=("Hiện chưa có chương trình ưu đãi nào dành cho anh/chị nên "
                  "tôi chưa có gì để giới thiệu hôm nay ạ. Anh/chị cần hỗ trợ "
                  "gì khác không?"),
            note="offers_none",
        )

    lines = ["Đây là các ưu đãi đang có trên tài khoản của anh/chị:", ""]
    for offer in offers:
        lines.append(f"**{offer.name}**")
        lines.append(offer.body)
        if offer.deeplink:
            lines.append(f"→ {offer.cta}: {offer.deeplink}")
        lines.append("")
    lines.append(
        f"_Do hệ thống chiến dịch của ngân hàng chọn lọc, cập nhật cách đây "
        f"{CAMPAIGNS.age_hours:.0f} giờ._" if CAMPAIGNS.age_hours is not None
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
        text=(f"\n\n---\n**While you're here:** {offer.body}\n\n"
              f"→ {offer.cta}: {offer.deeplink}"),
        note=f"proactive:{offer.campaign_id}",
    )


def _transactions(session: Session) -> FlowResult:
    customer = session.customer
    transactions = customer["transactions"][:5]
    if not transactions:
        return FlowResult(text="Tôi không thấy giao dịch nào gần đây trên tài khoản của anh/chị ạ.")
    lines = ["Các giao dịch gần nhất của anh/chị:"]
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
            text=("Tôi không thấy hồ sơ vay nào đang mở trên hồ sơ của anh/chị. "
                  "Nếu anh/chị vừa nộp tại quầy trong 24 giờ qua thì có thể hệ "
                  "thống chưa đồng bộ - tôi chuyển sang bộ phận tín dụng kiểm "
                  "tra giúp anh/chị nhé."),
            note="no_loan_on_file",
        )
    lines = ["Tình trạng hồ sơ của anh/chị như sau:"]
    for loan in loans:
        lines.append(
            f"- **{loan['product']}** ({loan['application_id']}) for "
            f"{_money(loan['amount'])}\n"
            f"  Status: **{loan['status']}** · submitted {loan['submitted_on']}\n"
            f"  {loan['note']}"
        )
    return FlowResult(text="\n".join(lines), note="loan_read_from_core")


# What each customer-facing card action does, and what it may act on. Freezing
# and reporting a card lost are two different requests that the previous version
# collapsed into one "block", which is why a customer who froze a card had no
# way back: the only transition that existed was terminal.
CARD_ACTIONS: dict[str, dict] = {
    "report_lost": {
        "verb": "báo mất và khoá",
        "from": ("active", "frozen", "dormant", "inactive"),
        "reversible": False,
        "confirm": ("Anh/chị xác nhận báo mất và khoá **thẻ {type} {mask}** ạ?\n\n"
                    "Thao tác này **không thể hoàn tác** - thẻ đã khoá do báo mất "
                    "sẽ không mở lại được, nên tôi sẽ phát hành thẻ thay thế "
                    "ngay cho anh/chị. Nếu anh/chị chỉ để quên và nghĩ sẽ tìm "
                    "lại được, hãy nhắn **tạm khoá thẻ** - loại này anh/chị tự "
                    "mở lại bất cứ lúc nào."),
        "none_left": "Hiện anh/chị không có thẻ nào ở trạng thái có thể khoá ạ.",
    },
    "freeze": {
        "verb": "tạm khoá",
        "from": ("active",),
        "reversible": True,
        "confirm": ("Tôi có thể tạm khoá **thẻ {type} {mask}** ngay cho anh/chị. "
                    "Mọi giao dịch trên thẻ sẽ dừng cho tới khi anh/chị mở lại, "
                    "và anh/chị mở lại ngay tại đây bất cứ lúc nào.\n\n"
                    "Anh/chị nhắn **có** để tôi thực hiện ạ."),
        "none_left": "Anh/chị hiện không có thẻ nào đang hoạt động để tạm khoá ạ.",
    },
    "unfreeze": {
        "verb": "mở khoá",
        "from": ("frozen",),
        "reversible": True,
        "confirm": ("Tôi sẽ mở khoá **thẻ {type} {mask}** cho anh/chị - thẻ dùng lại "
                    "được ngay sau đó.\n\nAnh/chị nhắn **có** để tôi thực hiện ạ."),
        "none_left": ("Hiện không có thẻ nào của anh/chị đang tạm khoá. Nếu "
                      "trước đó anh/chị đã báo mất thẻ thì thẻ ở trạng thái "
                      "khoá vĩnh viễn, không mở lại được - nhưng tôi có thể "
                      "kiểm tra giúp anh/chị thẻ thay thế ạ."),
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
            return FlowResult(text="Vâng, tôi giữ nguyên trạng thái thẻ cho anh/chị ạ.",
                              note=f"{action}_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(
                text=f"Anh/chị nhắn **có** để {spec['verb']} thẻ, hoặc **không** để giữ nguyên ạ.",
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
        return (f"Đã xong - **thẻ {card['type']} {card['mask']}** của anh/chị đã "
                f"tạm khoá từ bây giờ. Mã tham chiếu **{reference}**.\n\n"
                "Mọi giao dịch trên thẻ sẽ không thực hiện được, bao gồm cả các "
                "khoản thanh toán định kỳ. Khi cần dùng lại, anh/chị chỉ cần "
                "nhắn **mở khoá thẻ**, tôi mở ngay ạ.")
    if action == "unfreeze":
        return (f"**Thẻ {card['type']} {card['mask']}** của anh/chị đã hoạt động "
                f"trở lại - mã tham chiếu **{reference}**. Anh/chị dùng được ngay ạ.\n\n"
                "Nếu có giao dịch nào bị từ chối trong thời gian thẻ tạm khoá, "
                "anh/chị vui lòng đề nghị đơn vị bán hàng thực hiện lại giúp.")
    replacement = card.get("replacement") or {}
    return (f"Đã xong - **thẻ {card['type']} {card['mask']}** của anh/chị đã "
            f"khoá, có hiệu lực ngay. Mã tham chiếu **{reference}**.\n\n"
            f"Tôi đã phát hành thẻ thay thế: **{replacement.get('mask', 'thẻ mới')}**, "
            "dự kiến 5-7 ngày làm việc, miễn phí. Thẻ mới cần kích hoạt khi nhận "
            "được, và do số thẻ thay đổi nên anh/chị nhớ cập nhật lại các thanh "
            "toán định kỳ ạ.\n\n"
            "Anh/chị có giao dịch nào trên thẻ cũ cần tra soát không?")


def _card_choice_prompt(cards: list[dict], verb: str) -> str:
    options = "\n".join(
        f"- Thẻ {c['type']} {c['mask']}"
        + ("" if c["status"] == "active" else f" ({c['status']})")
        for c in cards)
    return (f"Anh/chị muốn {verb} thẻ nào ạ?\n{options}\n\n"
            "Vui lòng trả lời bằng 4 số cuối hoặc loại thẻ.")


def _match_card(cards: list[dict], text: str) -> dict | None:
    for card in cards:
        if card["mask"][-4:] in text:
            return card
    for card in cards:
        if card["type"].lower() in text.lower():
            return card
    return None


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
    if intent == "greeting":
        return FlowResult(
            text=("Xin chào! Tôi là trợ lý ảo của **ABC Bank**. Tôi có thể tra "
                  "cứu số dư và giao dịch, khoá hoặc mở khoá thẻ, kiểm tra hồ sơ "
                  "vay, và giải đáp về sản phẩm, biểu phí của ngân hàng.\n\n"
                  "Anh/chị cần hỗ trợ điều gì ạ?"),
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
            text="Tôi vẫn ở đây ạ. Anh/chị cần hỗ trợ gì thêm không?",
            note="smalltalk",
        )
    if intent == "goodbye":
        return FlowResult(
            text="Rất vui được hỗ trợ anh/chị. Chúc anh/chị một ngày tốt lành!", note="goodbye")
    if intent == "human_agent":
        return FlowResult(
            text="Of course.",
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
