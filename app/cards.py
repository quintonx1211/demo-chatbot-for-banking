r"""Customer card records - one card per customer, stored as CSV.

Deliberately a separate module from `app/db.py`: that module owns the
customer profile (who someone is), this one owns what card they hold and its
state. Different domains, different lifecycle - a card can be closed without
the customer record changing at all.

Because the catalogue maps each segment to exactly one card 1-1
(`data/products.json`), "which card does this customer have" is not a choice
- it is assigned at seed time from the customer's segment. What *is* a real
decision, made by the customer in conversation, is whether to close it or ask
for a different credit limit - and even the limit request is not auto-approved
here, only recorded. Approving a credit line is a real underwriting decision;
this demo does not pretend to make one.

Same atomic-write safety as `app/db.py`: every write holds `_lock` and goes
through a temp-file-then-replace so a reader never sees a half-written table.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import db

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_DIR = DATA_DIR / "db"
PRODUCTS_PATH = DATA_DIR / "products.json"

CARD_COLUMNS = ["customer_id", "card_id", "product_id", "status",
                "credit_limit", "opened_at"]
EVENT_COLUMNS = ["customer_id", "card_id", "action", "detail",
                 "session_id", "reference", "at"]

# Starting credit limit per card, by product id - invented for the demo
# (the client did not send limit figures), roughly scaled to each tier's
# annual fee. Flagged here rather than hidden so nobody mistakes it for real
# underwriting data.
DEFAULT_CREDIT_LIMIT = {
    "CARD-CLASSIC": 20_000_000,
    "CARD-PLATINUM": 50_000_000,
    "CARD-SIGNATURE": 150_000_000,
    "CARD-INFINITE": 500_000_000,
}

_SEEDED_OPENED_AT = "2025-01-15"

_lock = threading.RLock()


class TransitionError(ValueError):
    """A refused card action. The message is safe to show a customer."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _card_path() -> Path:
    # Separate file from db.py's "cards.csv" (debit/physical cards from accounts.json)
    # to avoid column-schema conflicts between the two card systems.
    return DB_DIR / "credit_cards.csv"


def _events_path() -> Path:
    return DB_DIR / "credit_card_events.csv"


def _read_csv(path: Path, columns: list[str], numeric: set[str] = frozenset()) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for column in numeric:
                value = (row.get(column) or "").strip()
                row[column] = float(value) if value else 0.0
            rows.append(row)
    return rows


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with _lock:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=DB_DIR, newline="", encoding="utf-8",
            prefix=f".{path.stem}.", suffix=".tmp")
        try:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in columns})
            handle.close()
            os.replace(handle.name, path)
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise


def _append_event(row: dict) -> None:
    events = _read_csv(_events_path(), EVENT_COLUMNS)
    events.append(row)
    _write_csv(_events_path(), EVENT_COLUMNS, events)


def _ensure() -> None:
    if not _card_path().exists():
        reset()


def reset() -> None:
    """Seed one active card per customer, from their segment's mapped product.

    Backs the console's reset button, same as `db.reset()` - a demo clicked
    through a few times needs a way back to a known state.
    """
    with _lock:
        products = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))["products"]
        by_segment = {p["segment"]: p for p in products}

        rows = []
        for customer in db._read("customers"):
            product = by_segment.get(customer["segment"])
            if not product:
                continue
            rows.append({
                "customer_id": customer["customer_id"],
                "card_id": f"CARD-{customer['customer_id'][-4:]}",
                "product_id": product["id"],
                "status": "active",
                "credit_limit": DEFAULT_CREDIT_LIMIT.get(product["id"], 20_000_000),
                "opened_at": _SEEDED_OPENED_AT,
            })
        _write_csv(_card_path(), CARD_COLUMNS, rows)
        _write_csv(_events_path(), EVENT_COLUMNS, [])


def get_card(customer_id: str | None) -> dict | None:
    """The one card belonging to this customer, or None."""
    if not customer_id:
        return None
    with _lock:
        _ensure()
        rows = _read_csv(_card_path(), CARD_COLUMNS, numeric={"credit_limit"})
    return next((r for r in rows if r["customer_id"] == customer_id), None)


def close_card(customer_id: str, session_id: str | None = None) -> dict:
    """Active -> closed. One-way, same principle as `report_lost` in the
    card state machine this replaces - a closed card is not un-closed in
    this demo; a customer who changes their mind applies for a new one."""
    with _lock:
        _ensure()
        rows = _read_csv(_card_path(), CARD_COLUMNS, numeric={"credit_limit"})
        card = next((r for r in rows if r["customer_id"] == customer_id), None)
        if card is None:
            raise TransitionError("Tôi không tìm thấy thẻ nào trên hồ sơ của bạn.")
        if card["status"] == "closed":
            raise TransitionError("Thẻ này đã được đóng trước đó rồi.")

        card["status"] = "closed"
        now = _now()
        reference = f"CLS-{card['card_id'][-4:]}-{datetime.now(timezone.utc):%m%d%H%M%S}"
        _write_csv(_card_path(), CARD_COLUMNS, rows)
        _append_event({
            "customer_id": customer_id, "card_id": card["card_id"],
            "action": "close", "detail": "active -> closed",
            "session_id": session_id, "reference": reference, "at": now,
        })
        result = dict(card)
        result["reference"] = reference
        return result


def request_limit_adjustment(customer_id: str, requested_limit: float,
                             session_id: str | None = None) -> dict:
    """Records the request - does not change `credit_limit`. Approving a
    credit line is a real underwriting decision that needs income/credit
    history this demo does not have; the honest answer is "request logged",
    not a number the bot invented."""
    with _lock:
        _ensure()
        card = get_card(customer_id)
        if card is None:
            raise TransitionError("Tôi không tìm thấy thẻ nào trên hồ sơ của bạn.")
        if card["status"] != "active":
            raise TransitionError("Thẻ này hiện không ở trạng thái hoạt động nên chưa thể đổi hạn mức.")

        now = _now()
        reference = f"LMT-{card['card_id'][-4:]}-{datetime.now(timezone.utc):%m%d%H%M%S}"
        _append_event({
            "customer_id": customer_id, "card_id": card["card_id"],
            "action": "limit_adjust_requested",
            "detail": f"current {card['credit_limit']:.0f} -> requested {requested_limit:.0f}",
            "session_id": session_id, "reference": reference, "at": now,
        })
        return {"reference": reference, "current_limit": card["credit_limit"],
                "requested_limit": requested_limit}


def stats() -> dict:
    with _lock:
        _ensure()
        rows = _read_csv(_card_path(), CARD_COLUMNS, numeric={"credit_limit"})
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {"cards": len(rows), "by_status": dict(sorted(by_status.items()))}
