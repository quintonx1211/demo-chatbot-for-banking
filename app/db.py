r"""Customer records, stored as CSV.

CSV rather than a real database, deliberately, because this is a demo. The
file under `data/db/` opens in Excel, so a client can see the row that backs
an answer without a query tool.

What that choice costs, stated plainly rather than discovered later:

  * **No concurrency from other processes.** Two servers on the same folder
    would overwrite each other. One server is the assumption.
  * **Whole-file rewrites.** Every write rewrites the table. Fine at nine
    customers; not how this would be built for two hundred thousand.

Within one process it is made safe: every read and write holds `_lock`, and
writes go to a temporary file that is then moved over the original, so a
reader sees either the old file or the new one and never a half-written one.
"""

from __future__ import annotations

import csv
import json
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_DIR = DATA_DIR / "db"
SEED_PATH = DATA_DIR / "customers_seed.json"

# Column order is fixed here rather than taken from whatever dict is written,
# so the file stays diffable and a human editing it in Excel gets the columns
# they expect back.
COLUMNS: list[str] = [
    "customer_id", "name", "given_name", "phone_last4", "national_id_last4",
    "segment", "age", "stated_interests",
]

NUMERIC = {"age"}
# Stored as ";"-joined strings in the CSV cell, parsed back to a list on read.
LIST_COLUMNS = {"stated_interests"}

_lock = threading.RLock()


def _path() -> Path:
    return DB_DIR / "customers.csv"


def _read() -> list[dict]:
    """Every customer row, with numeric and list columns converted."""
    with _lock:
        _ensure()
        rows = []
        with _path().open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                for column in NUMERIC:
                    value = (row.get(column) or "").strip()
                    row[column] = float(value) if value else 0.0
                for column in LIST_COLUMNS:
                    value = (row.get(column) or "").strip()
                    row[column] = value.split(";") if value else []
                rows.append(row)
        return rows


def _write(rows: list[dict]) -> None:
    """Replace the table atomically.

    Written to a temporary file in the same directory and then moved over the
    target: `os.replace` is atomic on both POSIX and Windows, so a reader can
    never observe a partially written table.
    """
    import os
    import tempfile

    with _lock:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=DB_DIR, newline="", encoding="utf-8",
            prefix=".customers.", suffix=".tmp")
        try:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                out = dict(row)
                for column in LIST_COLUMNS:
                    out[column] = ";".join(out.get(column) or [])
                writer.writerow(out)
            handle.close()
            os.replace(handle.name, _path())
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise


def _ensure() -> None:
    """Create and seed the file on first use."""
    if _path().exists():
        return
    reset()


# -- seeding --------------------------------------------------------------

def reset() -> None:
    """Rewrite the customer table from the fixture.

    Backs the console's reset button - a demo run through a few times needs a
    way back to a known state.
    """
    with _lock:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        customers = [
            {
                "customer_id": c["customer_id"],
                "name": c["name"],
                "given_name": c.get("given_name") or c["name"].split()[-1],
                "phone_last4": c["phone_last4"],
                "national_id_last4": c["national_id_last4"],
                "segment": c.get("segment", "MASS"),
                "age": c.get("age", 0),
                "stated_interests": c.get("stated_interests", []),
            }
            for c in raw["customers"]
        ]
        _write(customers)


# -- reads ------------------------------------------------------------------

def customer_ids() -> list[str]:
    return [row["customer_id"] for row in _read()]


def get_customer(customer_id: str | None) -> dict | None:
    """A customer's full profile.

    Returns a fresh dict each call, read from the file, so a caller mutating
    what they get back cannot change another session's view.
    """
    if not customer_id:
        return None
    return next((row for row in _read() if row["customer_id"] == customer_id), None)


def find_by_credentials(phone_last4: str, national_id_last4: str) -> dict | None:
    """Both factors, matched against the same customer."""
    return next((row for row in _read()
                 if row["phone_last4"] == phone_last4
                 and row["national_id_last4"] == national_id_last4), None)


def stats() -> dict:
    """Counts for the console, so the demo's data state is visible."""
    rows = _read()
    by_segment: dict[str, int] = {}
    for row in rows:
        by_segment[row["segment"]] = by_segment.get(row["segment"], 0) + 1
    return {
        "path": str(DB_DIR),
        "format": "csv",
        "customers": len(rows),
        "by_segment": dict(sorted(by_segment.items())),
    }
