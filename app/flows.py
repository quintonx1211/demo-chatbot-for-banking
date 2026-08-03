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

from .session import CUSTOMERS, Session

# Flows that must not run for an unverified caller.
PROTECTED_FLOWS = {"balance_inquiry", "block_card", "loan_status", "transaction_history"}


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
    """Start (or continue) the identity check before a protected flow runs."""
    session.pending_flow = "verify"
    session.slots["target_intent"] = intent
    return FlowResult(
        text=(
            "Before I can look at account details I need to verify your identity. "
            "Please give me the **last 4 digits of your registered phone number**.\n\n"
            "_Demo hint: try `4471` (Maria Alvarez) or `9032` (Daniel Okafor)._"
        ),
        note="verification_started",
    )


def _handle_verification(session: Session, text: str) -> FlowResult:
    digits = re.findall(r"\b\d{4}\b", text)
    if not digits:
        return FlowResult(
            text="I need exactly 4 digits - the last 4 of your registered phone number.",
            note="verification_retry",
        )

    match = next((c for c in CUSTOMERS if c["phone_last4"] == digits[0]), None)
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
            text=(f"Those digits don't match our records (attempt {attempts} of 3). "
                  "Please try again."),
            note="verification_retry",
        )

    session.customer_id = match["customer_id"]
    session.verified = True
    target = session.slots.get("target_intent")
    session.reset_flow()

    greeting = f"Thanks, {match['name'].split()[0]} - you're verified. "
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


def continue_pending(session: Session, text: str) -> FlowResult | None:
    """Resume a mid-conversation flow before the classifier gets a say.

    Slot answers like "4471" or "yes" carry no intent signal, so an in-progress
    flow must take priority over classification.
    """
    if session.pending_flow == "verify":
        return _handle_verification(session, text)
    if session.pending_flow == "block_card":
        return _block_card(session, text)
    return None
