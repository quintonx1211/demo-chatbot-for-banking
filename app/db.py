r"""Customer, account and card records, stored as CSV.

CSV rather than a real database, deliberately, because this is a demo. The
files under `data/db/` open in Excel, so a client can be shown the row that
changed the moment after the assistant changes it, and a colleague can edit a
balance without a query tool. For a demo that is worth more than referential
integrity.

What that choice costs, stated plainly rather than discovered later:

  * **No transactions.** A card status change and its audit row are two writes.
    A crash between them leaves the change without its log entry.
  * **No concurrency from other processes.** Two servers on the same folder
    would overwrite each other. One server is the assumption.
  * **Whole-file rewrites.** Every write rewrites the table. Fine at fifteen
    customers; not how this would be built for two hundred thousand.

Within one process it is made safe: every read and write holds `_lock`, and
writes go to a temporary file that is then moved over the original, so a reader
sees either the old file or the new one and never a half-written one. That is
the difference between a demo that survives being clicked quickly and one that
corrupts its own fixture during a client call.

The card lifecycle is a state machine, enforced in `set_card_status` rather
than implied by whichever caller happens to be writing:

    inactive --activate--> active <--unfreeze-- frozen
                             |  \---freeze----->|
    dormant --reactivate--> active              |
                             \--report_lost--> blocked  (terminal)
                                                  \--> replacement card issued

`blocked` is deliberately terminal. A card reported lost or stolen is not
un-reported; the bank issues a new one. That is why blocking creates a
replacement row - so "block my card" leaves the customer with a working card
instead of a dead end - and why a *reversible* freeze exists as its own
transition for the customer who has merely mislaid it.

Every transition appends to `card_events.csv`. An account action a bank cannot
evidence afterwards may as well not have happened.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_DIR = DATA_DIR / "db"
SEED_PATH = DATA_DIR / "accounts.json"

# Column order is fixed here rather than taken from whatever dict is written,
# so the files stay diffable and a human editing one in Excel gets the columns
# they expect back.
COLUMNS: dict[str, list[str]] = {
    "customers": ["customer_id", "name", "given_name", "dob", "phone_last4",
                  "national_id_last4", "segment", "profile"],
    "accounts": ["account_id", "customer_id", "type", "mask", "balance",
                 "available", "currency"],
    "cards": ["card_id", "customer_id", "type", "mask", "status",
              "linked_account", "replaces", "updated_at"],
    "loans": ["application_id", "customer_id", "product", "amount", "status",
              "submitted_on", "note"],
    "transactions": ["customer_id", "date", "description", "amount"],
    "card_events": ["card_id", "customer_id", "from_status", "to_status",
                    "action", "session_id", "actor", "reference", "at"],
}

# Columns that must come back as numbers. Everything in a CSV is a string on
# the way in, and a balance compared or formatted as text is a bug that hides
# until the number is large enough to sort wrongly.
NUMERIC: dict[str, set[str]] = {
    "accounts": {"balance", "available"},
    "loans": {"amount"},
    "transactions": {"amount"},
}

# What each action is allowed to do, and from where. A transition absent from
# this table cannot happen, whatever a caller asks for.
TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    "activate":   (frozenset({"inactive"}), "active"),
    "reactivate": (frozenset({"dormant"}), "active"),
    "freeze":     (frozenset({"active"}), "frozen"),
    "unfreeze":   (frozenset({"frozen"}), "active"),
    # A lost or stolen card is a one-way door; the replacement is a new card.
    "report_lost": (frozenset({"active", "frozen", "dormant", "inactive"}),
                    "blocked"),
}

# Statuses a customer can transact on.
USABLE = frozenset({"active"})


class TransitionError(ValueError):
    """A refused card transition. The message is safe to show a customer."""


_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- CSV plumbing ---------------------------------------------------------

def _path(table: str) -> Path:
    return DB_DIR / f"{table}.csv"


def _read(table: str) -> list[dict]:
    """Every row of a table, with numeric columns converted."""
    with _lock:
        _ensure()
        numeric = NUMERIC.get(table, set())
        rows = []
        with _path(table).open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                for column in numeric:
                    value = (row.get(column) or "").strip()
                    row[column] = float(value) if value else 0.0
                # An empty CSV cell is the absence of a value, not the string
                # "". Callers check `if card["replaces"]`, and "" is falsy by
                # luck rather than by meaning - None says what is intended.
                for key, value in list(row.items()):
                    if value == "":
                        row[key] = None
                rows.append(row)
        return rows


def _write(table: str, rows: list[dict]) -> None:
    """Replace a table atomically.

    Written to a temporary file in the same directory and then moved over the
    target: `os.replace` is atomic on both POSIX and Windows, so a reader can
    never observe a partially written table. Writing in place would leave a
    truncated file visible for the duration of the write, which on a demo being
    clicked through quickly is not a theoretical window.
    """
    with _lock:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        columns = COLUMNS[table]
        handle = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=DB_DIR, newline="", encoding="utf-8",
            prefix=f".{table}.", suffix=".tmp")
        try:
            writer = csv.DictWriter(handle, fieldnames=columns,
                                    extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: ("" if row.get(c) is None else row.get(c))
                                 for c in columns})
            handle.close()
            os.replace(handle.name, _path(table))
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise


def _append(table: str, row: dict) -> None:
    with _lock:
        _ensure()
        with _path(table).open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=COLUMNS[table],
                           extrasaction="ignore").writerow(
                {c: ("" if row.get(c) is None else row.get(c))
                 for c in COLUMNS[table]})


def _ensure() -> None:
    """Create and seed the files on first use."""
    if _path("customers").exists():
        return
    reset()


# -- seeding --------------------------------------------------------------

def reset() -> None:
    """Rewrite every table from the fixture.

    Backs the console's reset button. A demo that has had cards blocked
    through it needs a way back to a known state; without one, every run
    starts further from the fixture than the last.
    """
    with _lock:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        now = _now()

        customers, accounts, cards, loans, transactions = [], [], [], [], []
        for customer in raw["customers"]:
            customers.append({
                "customer_id": customer["customer_id"],
                "name": customer["name"],
                "given_name": (customer.get("given_name")
                               or customer["name"].split()[-1]),
                "dob": customer.get("dob"),
                "phone_last4": customer["phone_last4"],
                "national_id_last4": customer["national_id_last4"],
                "segment": customer.get("segment", "MASS"),
                "profile": customer.get("profile", ""),
            })
            for account in customer.get("accounts", []):
                accounts.append({**account,
                                 "customer_id": customer["customer_id"],
                                 "currency": account.get("currency", "VND")})
            for card in customer.get("cards", []):
                cards.append({**card, "customer_id": customer["customer_id"],
                              "updated_at": now})
            for loan in customer.get("loans", []):
                loans.append({**loan, "customer_id": customer["customer_id"]})
            for txn in customer.get("transactions", []):
                transactions.append({**txn,
                                     "customer_id": customer["customer_id"]})

        _write("customers", customers)
        _write("accounts", accounts)
        _write("cards", cards)
        _write("loans", loans)
        _write("transactions", transactions)
        _write("card_events", [])


# -- reads ----------------------------------------------------------------

def customer_ids() -> list[str]:
    return [row["customer_id"] for row in _read("customers")]


def get_customer(customer_id: str | None) -> dict | None:
    """A customer with their accounts, cards, loans and transactions.

    Returns a fresh dict each call, read from the files. Callers may mutate
    what they get back without changing another session's view - which was
    exactly the failure mode of the shared in-memory list this replaced.
    """
    if not customer_id:
        return None
    customer = next((row for row in _read("customers")
                     if row["customer_id"] == customer_id), None)
    if customer is None:
        return None

    def owned(table: str, sort_key: str, reverse: bool = False) -> list[dict]:
        rows = [r for r in _read(table) if r["customer_id"] == customer_id]
        return sorted(rows, key=lambda r: r[sort_key] or "", reverse=reverse)

    customer["accounts"] = owned("accounts", "account_id")
    customer["cards"] = owned("cards", "card_id")
    customer["loans"] = owned("loans", "application_id")
    customer["transactions"] = owned("transactions", "date", reverse=True)
    return customer


def find_by_credentials(phone_last4: str, national_id_last4: str) -> dict | None:
    """Both factors, matched against the same customer."""
    match = next((row for row in _read("customers")
                  if row["phone_last4"] == phone_last4
                  and row["national_id_last4"] == national_id_last4), None)
    return get_customer(match["customer_id"]) if match else None


def get_card(card_id: str) -> dict | None:
    return next((row for row in _read("cards")
                 if row["card_id"] == card_id), None)


def card_events(customer_id: str | None = None, limit: int = 50) -> list[dict]:
    """Most recent first. The file is append-only, so newest is last."""
    rows = _read("card_events")
    if customer_id:
        rows = [r for r in rows if r["customer_id"] == customer_id]
    return list(reversed(rows))[:limit]


# -- writes ---------------------------------------------------------------

def set_card_status(card_id: str, action: str, session_id: str | None = None,
                    actor: str = "customer") -> dict:
    """Apply `action` to a card, or refuse and say why.

    The point of routing every change through one function is that the legal
    transitions live in one place. A caller cannot invent a new one by
    assigning to a dict, which is how the previous version let a card reach
    `blocked` with nothing able to move it out again.
    """
    if action not in TRANSITIONS:
        raise TransitionError(f"Unknown card action {action!r}.")
    allowed_from, to_status = TRANSITIONS[action]

    with _lock:
        cards = _read("cards")
        card = next((c for c in cards if c["card_id"] == card_id), None)
        if card is None:
            raise TransitionError("I can't find that card on your profile.")

        current = card["status"]
        if current == to_status:
            raise TransitionError(f"That card is already {to_status}.")
        if current not in allowed_from:
            raise TransitionError(_refusal(current, action))

        now = _now()
        card["status"] = to_status
        card["updated_at"] = now
        reference = _reference(action, card_id)

        replacement = None
        if action == "report_lost":
            replacement = _build_replacement(cards, card, now)
            cards.append(replacement)

        # Cards first, then the log. If the process dies between the two the
        # state is right and the audit row is missing, which is recoverable by
        # reading the file. The other order would log an action that never
        # happened, which is not.
        _write("cards", cards)
        _append("card_events", {
            "card_id": card_id, "customer_id": card["customer_id"],
            "from_status": current, "to_status": to_status, "action": action,
            "session_id": session_id, "actor": actor, "reference": reference,
            "at": now,
        })
        if replacement:
            _append("card_events", {
                "card_id": replacement["card_id"],
                "customer_id": card["customer_id"], "from_status": None,
                "to_status": "inactive", "action": "issue_replacement",
                "session_id": session_id, "actor": "system",
                "reference": f"REP-{replacement['card_id'][-4:]}", "at": now,
            })

    result = dict(card)
    result["reference"] = reference
    result["replacement"] = (
        {k: replacement[k] for k in ("card_id", "mask", "type", "status")}
        if replacement else None)
    return result


def _refusal(current: str, action: str) -> str:
    """Why a transition was refused, in words a customer can act on."""
    if current == "blocked":
        return ("That card is blocked because it was reported lost or stolen, "
                "and a blocked card can't be reopened - that's a security rule, "
                "not a limitation of what I can do here. A replacement has "
                "already been issued and will arrive in 5-7 business days.")
    if current == "inactive" and action in ("freeze", "unfreeze"):
        return ("That card hasn't been activated yet, so there's nothing to "
                "freeze. I can help you activate it instead.")
    if current == "dormant" and action == "unfreeze":
        return ("That card is dormant rather than frozen. I can walk you "
                "through reactivating it.")
    if action == "unfreeze":
        return "That card isn't frozen, so there's nothing to unfreeze."
    if action == "activate":
        return "That card is already activated."
    return f"I can't {action.replace('_', ' ')} a card that is {current}."


def _reference(action: str, card_id: str) -> str:
    prefix = {"report_lost": "BLK", "freeze": "FRZ", "unfreeze": "UNF",
              "activate": "ACT", "reactivate": "RAC"}[action]
    return f"{prefix}-{card_id[-4:]}-{datetime.now(timezone.utc):%m%d%H%M}"


def _build_replacement(cards: list[dict], card: dict, now: str) -> dict:
    """A blocked card is terminal, so the customer needs a new one.

    Issued `inactive`, not `active`: a card that has not physically arrived
    should not be spendable, and it gives the activation flow something real to
    act on afterwards. The number is new, and has to *look* new - the customer
    is told to update recurring payments to it, so a mask with a letter in it
    reads as a bug rather than as a card.
    """
    taken = {c["card_id"] for c in cards}
    digits = "".join(ch for ch in card["card_id"] if ch.isdigit())[-4:] or "0"
    base = int(digits)
    for step in range(1, 10000):
        candidate = f"CRD-{(base + step * 137) % 10000:04d}"
        if candidate not in taken:
            return {
                "card_id": candidate, "customer_id": card["customer_id"],
                "type": card["type"], "mask": f"****{candidate[-4:]}",
                "status": "inactive", "linked_account": card["linked_account"],
                "replaces": card["card_id"], "updated_at": now,
            }
    raise TransitionError("Unable to allocate a replacement card number.")


def stats() -> dict:
    """Counts for the console, so the demo's data state is visible."""
    cards = _read("cards")
    by_status: dict[str, int] = {}
    for card in cards:
        by_status[card["status"]] = by_status.get(card["status"], 0) + 1
    return {
        "path": str(DB_DIR),
        "format": "csv",
        "customers": len(_read("customers")),
        "cards": len(cards),
        "cards_by_status": dict(sorted(by_status.items())),
        "card_events": len(_read("card_events")),
    }
