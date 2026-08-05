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
            "Happy to help with that. First, a quick security check so I know "
            "it's really you.\n\n"
            "Could you send me the **last 4 digits of your registered phone "
            "number** and the **last 4 digits of your national ID (ID)**, "
            "with a space between them?"
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
            text=("Not quite - I need just the two 4-digit codes on their own: "
                  "the last 4 of your phone number, then the last 4 of your "
                  "ID. Something like `1234 5678`.\n\n"
                  "Please don't send your full card number or PIN - I'll never "
                  "need either of those."),
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
                text=("I'm sorry - I haven't been able to verify those details, "
                      "and I'm not able to keep trying from here. Let me pass "
                      "you to a colleague who can sort this out properly."),
                escalate=True,
                escalation_reason="Identity verification failed three times",
                note="verification_failed",
            )
        return FlowResult(
            text=(f"Those details don't match our records (attempt {attempts} of 3). "
                  "Please try again with both codes."),
            note="verification_retry",
        )

    session.customer_id = match["customer_id"]
    session.verified = True
    target = session.slots.get("target_intent")
    session.reset_flow()

    # Cross-session recall lands here and nowhere earlier: before this line the
    # session has no verified customer, so there is nobody to remember.
    recalled = memory.store.summary(match["customer_id"])
    greeting = f"Thanks, {_first_name(match)} - you're verified. "
    if recalled:
        greeting += recalled + " "
    if target:
        follow_up = handle(session, target, "")
        return FlowResult(text=greeting + follow_up.text,
                          escalate=follow_up.escalate,
                          escalation_reason=follow_up.escalation_reason,
                          note="verified_then_" + target)
    return FlowResult(text=greeting + "How can I help?", note="verified")


# -- account flows --------------------------------------------------------

def _balance(session: Session) -> FlowResult:
    customer = session.customer
    lines = [f"Here are your balances as of today, {_first_name(customer)}:"]
    for account in customer["accounts"]:
        lines.append(
            f"- **{account['type']}** {account['mask']} - "
            f"balance {_money(account['balance'], account['currency'])}, "
            f"available {_money(account['available'], account['currency'])}"
        )
    return FlowResult(text="\n".join(lines), note="balance_read_from_core")


def _account_summary(session: Session) -> FlowResult:
    """Answer "who am I" from the record, once identity is verified.

    Previously this escalated: a reasonable question, asked by a customer the
    system had already verified, handed to a human for data it was holding.
    """
    customer = session.customer
    lines = [
        f"You're **{customer['name']}** ({customer['customer_id']}), "
        f"verified on this chat.",
        "",
        "Here's what you hold with us:",
    ]
    for account in customer["accounts"]:
        lines.append(f"- **{account['type']}** {account['mask']}")
    for card in customer["cards"]:
        state = "" if card["status"] == "active" else f" - {card['status']}"
        lines.append(f"- **{card['type']} card** {card['mask']}{state}")
    for loan in customer.get("loans", []):
        lines.append(f"- **{loan['product']}** application {loan['application_id']} "
                     f"- {loan['status']}")
    lines.append("")
    lines.append("I can go into any of these in more detail.")
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
            text=("All the cards on your profile are already active. If a "
                  "payment was declined, that's a different issue and I can "
                  "look into it."),
            note="activation_none_pending",
        )

    card = inactive[0]
    return FlowResult(
        text=(
            f"Your **{card['type']} card {card['mask']}** is issued but not yet "
            f"activated.\n\n"
            f"**{campaign.get('cta', 'Activate in the mobile app')}** - "
            f"{campaign.get('deeplink', '')}\n\n"
            f"Or: {campaign.get('sms_alternative', 'call the number on the card')}.\n\n"
            "_I won't ever ask you for a one-time passcode in this chat. If "
            "anything claiming to be us does, it isn't us._"
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
            text=("You're not in any current campaigns, so I don't have an "
                  "offer to show you today. Anything else I can help with?"),
            note="offers_none",
        )

    lines = ["Here's what's available on your account today:", ""]
    for offer in offers:
        lines.append(f"**{offer.name}**")
        lines.append(offer.body)
        if offer.deeplink:
            lines.append(f"→ {offer.cta}: {offer.deeplink}")
        lines.append("")
    lines.append(
        f"_Selected by the bank's campaign system, last updated "
        f"{CAMPAIGNS.age_hours:.0f} hours ago._" if CAMPAIGNS.age_hours is not None
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
        return FlowResult(text="I don't see any recent transactions on your accounts.")
    lines = ["Your most recent transactions:"]
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
            text=("I don't see any open loan applications under your profile. "
                  "If you applied in a branch in the last 24 hours it may not "
                  "have synced yet - I can pass you to the lending team to check."),
            note="no_loan_on_file",
        )
    lines = ["Here's where your application stands:"]
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
        "verb": "report lost and block",
        "from": ("active", "frozen", "dormant", "inactive"),
        "reversible": False,
        "confirm": ("Just to confirm: report your **{type} card {mask}** lost "
                    "and block it?\n\nThis one can't be undone - a blocked card "
                    "is never reopened, so I'll order you a replacement at the "
                    "same time. If you've only mislaid it and think it'll turn "
                    "up, say **freeze** instead and you can unfreeze it "
                    "yourself later."),
        "none_left": "You don't have a card I can block right now.",
    },
    "freeze": {
        "verb": "freeze",
        "from": ("active",),
        "reversible": True,
        "confirm": ("I can freeze your **{type} card {mask}** straight away. "
                    "Nothing will go through on it until you unfreeze it, and "
                    "you can do that here any time.\n\nReply **yes** to freeze "
                    "it."),
        "none_left": "You don't have an active card to freeze.",
    },
    "unfreeze": {
        "verb": "unfreeze",
        "from": ("frozen",),
        "reversible": True,
        "confirm": ("Ready to unfreeze your **{type} card {mask}** - it'll work "
                    "again immediately.\n\nReply **yes** and I'll do it."),
        "none_left": ("None of your cards are frozen at the moment. If a card "
                      "was reported lost it's blocked rather than frozen, and "
                      "that one can't be reopened - but I can check the "
                      "replacement for you."),
    },
}

_YES_RE = re.compile(r"\b(yes|yeah|yep|confirm|correct|do it|go ahead|please)\b", re.I)
_NO_RE = re.compile(r"\b(no|nope|cancel|stop|don't|dont|wait)\b", re.I)


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
            return FlowResult(text="No problem - I've left the card as it is.",
                              note=f"{action}_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(
                text=f"Reply **yes** to {spec['verb']} the card, or **no** to leave it.",
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
        return (f"Done - your **{card['type']} card {card['mask']}** is frozen as "
                f"of now. Reference **{reference}**.\n\n"
                "Nothing will go through on it, including recurring payments. "
                "Just say **unfreeze my card** whenever you want it back, and "
                "I'll switch it on straight away.")
    if action == "unfreeze":
        return (f"Your **{card['type']} card {card['mask']}** is active again - "
                f"reference **{reference}**. You can use it right now.\n\n"
                "If any payment was declined while it was frozen, the merchant "
                "will need to take it again.")
    replacement = card.get("replacement") or {}
    return (f"Done - your **{card['type']} card {card['mask']}** is blocked "
            f"effective immediately. Reference **{reference}**.\n\n"
            f"I've ordered a replacement: **{replacement.get('mask', 'a new card')}**, "
            "arriving in 5-7 business days at no charge. It'll need activating "
            "when it lands, and because the number changes you'll want to update "
            "any recurring payments.\n\n"
            "Is there anything on the old card you want to query?")


def _card_choice_prompt(cards: list[dict], verb: str) -> str:
    options = "\n".join(
        f"- {c['type']} card {c['mask']}"
        + ("" if c["status"] == "active" else f" ({c['status']})")
        for c in cards)
    return (f"Which card should I {verb}?\n{options}\n\n"
            "Reply with the last 4 digits or the card type.")


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
            text=("Hello! I'm the virtual assistant for ABC Bank. I can "
                  "check balances and transactions, block a lost card, look up a "
                  "loan application, or answer questions about our products and "
                  "fees. What do you need?"),
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
            text="I'm here. What else can I help you with?",
            note="smalltalk",
        )
    if intent == "goodbye":
        return FlowResult(
            text="Happy to help. Have a good day!", note="goodbye")
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
