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
from .session import CUSTOMERS, Session

CAMPAIGNS = CampaignBook()

# Flows that must not run for an unverified caller.
PROTECTED_FLOWS = {"balance_inquiry", "block_card", "loan_status",
                   "transaction_history", "account_summary",
                   "activate_card", "card_offers"}


@dataclass
class FlowResult:
    text: str
    handled: bool = True
    escalate: bool = False
    escalation_reason: str | None = None
    note: str = ""


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


# -- verification ---------------------------------------------------------

def _verification_prompt(session: Session, intent: str) -> FlowResult:
    """Start (or continue) the identity check before a protected flow runs.

    Two factors, asked for and checked together: the phone number alone was a
    4-digit space with no lockout wider than one session - a customer (or an
    attacker) could open a fresh session after every third guess and keep
    dialling. A second, independent factor - the national ID (CCCD) - does
    not fix that on its own, but it takes the search space from "guess one
    4-digit code" to "guess two 4-digit codes for the same person at once",
    which is the cheapest real improvement available without a true step-up
    channel (OTP to the registered device) standing in for a demo.
    """
    session.pending_flow = "verify"
    session.slots["target_intent"] = intent
    return FlowResult(
        text=(
            "Before I can look at account details I need to verify your identity. "
            "Please give me the **last 4 digits of your registered phone number** "
            "and the **last 4 digits of your national ID (CCCD)**, separated by a space.\n\n"
            "_Demo hint: Maria `4471 0512` · Daniel `9032 8847` · Linh `3390 2266`_"
        ),
        note="verification_started",
    )


def _handle_verification(session: Session, text: str) -> FlowResult:
    digits = re.findall(r"\b\d{4}\b", text)
    if len(digits) < 2:
        return FlowResult(
            text=("I need two 4-digit codes - the last 4 digits of your phone "
                  "number, then the last 4 digits of your national ID (CCCD). "
                  "For example: `4471 0512`."),
            note="verification_retry",
        )

    phone, national_id = digits[0], digits[1]
    # Both factors have to match the same customer. The failure message below
    # does not say which one was wrong - telling an attacker "the phone
    # matched but the ID didn't" turns two independent secrets into one,
    # guessed a factor at a time.
    match = next(
        (c for c in CUSTOMERS
         if c["phone_last4"] == phone and c["national_id_last4"] == national_id),
        None,
    )
    if not match:
        attempts = int(session.slots.get("verify_attempts", "0")) + 1
        session.slots["verify_attempts"] = str(attempts)
        if attempts >= 3:
            session.reset_flow()
            return FlowResult(
                text="I wasn't able to verify those details.",
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
    greeting = f"Thanks, {match['name'].split()[0]} - you're verified. "
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
    lines = [f"Here are your balances as of today, {customer['name'].split()[0]}:"]
    for account in customer["accounts"]:
        lines.append(
            f"- **{account['type']}** {account['mask']} - "
            f"balance {_money(account['balance'])}, "
            f"available {_money(account['available'])}"
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
        state = "" if card["status"] == "active" else f" — {card['status']}"
        lines.append(f"- **{card['type']} card** {card['mask']}{state}")
    for loan in customer.get("loans", []):
        lines.append(f"- **{loan['product']}** application {loan['application_id']} "
                     f"— {loan['status']}")
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
            f"**{campaign.get('cta', 'Activate in the mobile app')}** — "
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

    Only service-blocking situations qualify — a card that will not work
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


def _block_card(session: Session, text: str) -> FlowResult:
    """Two-step confirmation: pick the card, then confirm the irreversible block."""
    customer = session.customer
    active = [c for c in customer["cards"] if c["status"] == "active"]

    if not active:
        return FlowResult(text="All cards on your profile are already blocked.",
                          note="no_active_cards")

    stage = session.slots.get("block_stage")

    if stage == "confirm":
        if re.search(r"\b(yes|yeah|yep|confirm|correct|do it|go ahead)\b", text, re.I):
            card = next(c for c in customer["cards"]
                        if c["card_id"] == session.slots["card_id"])
            card["status"] = "blocked"          # writes to the mock core banking record
            session.reset_flow()
            return FlowResult(
                text=(
                    f"Done - your **{card['type']} card {card['mask']}** is blocked "
                    f"effective immediately. Reference **BLK-{card['card_id'][-4:]}"
                    f"-{session.session_id[-4:]}**.\n\n"
                    "A replacement is on its way and arrives in 5–7 business days at "
                    "no charge. The card number will change, so any recurring payments "
                    "on the old card need updating. Anything else I can help with?"
                ),
                note=f"card_blocked:{card['card_id']}",
            )
        if re.search(r"\b(no|nope|cancel|stop|don't|dont)\b", text, re.I):
            session.reset_flow()
            return FlowResult(text="No problem - I've left the card active.",
                              note="block_cancelled")
        return FlowResult(text="Please reply **yes** to confirm the block, or **no** to cancel.",
                          note="block_confirm_retry")

    if stage == "select":
        chosen = _match_card(active, text)
        if not chosen:
            return FlowResult(text=_card_choice_prompt(active), note="block_select_retry")
        session.slots["card_id"] = chosen["card_id"]
        session.slots["block_stage"] = "confirm"
        return FlowResult(
            text=(f"Just to confirm: block your **{chosen['type']} card "
                  f"{chosen['mask']}**? This is immediate and cannot be undone. "
                  "Reply **yes** to proceed."),
            note="block_confirm_prompt",
        )

    # First entry into the flow.
    session.pending_flow = "block_card"
    if len(active) == 1:
        session.slots["card_id"] = active[0]["card_id"]
        session.slots["block_stage"] = "confirm"
        return FlowResult(
            text=(f"I can block your **{active[0]['type']} card {active[0]['mask']}** "
                  "right away. This is immediate and cannot be undone - reply "
                  "**yes** to proceed."),
            note="block_confirm_prompt",
        )
    session.slots["block_stage"] = "select"
    return FlowResult(text=_card_choice_prompt(active), note="block_select_prompt")


def _card_choice_prompt(cards: list[dict]) -> str:
    options = "\n".join(f"- {c['type']} card {c['mask']}" for c in cards)
    return ("You have more than one active card. Which one should I block?\n"
            f"{options}\n\nReply with the last 4 digits or the card type.")


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
        return _block_card(session, text)
    if intent == "greeting":
        return FlowResult(
            text=("Hello! I'm the virtual assistant for Regional Trust Bank. I can "
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

    Slot answers like "4471 0512" or "yes" carry no intent signal, so an in-progress
    flow must take priority over classification.
    """
    if session.pending_flow == "verify":
        return _handle_verification(session, text)
    if session.pending_flow == "block_card":
        return _block_card(session, text)
    return None
