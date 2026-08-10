"""Generate the customer fixture that seeds the database.

Written as a script rather than hand-edited JSON so the fixture stays internally
consistent: masks match card ids, linked accounts exist, credentials are unique,
and the card-status mix actually covers the flows that have to be demonstrated.
Hand-maintaining fifteen customers across five tables is how a fixture ends up
with a card pointing at an account that was renamed three edits ago.

    python make_customers.py            # rewrite data/accounts.json
    python make_customers.py --check    # verify the existing file, change nothing

Deterministic: a fixed seed, so the same credentials come out every run and the
test script does not have to be rewritten each time this is regenerated.

Every value here is invented. The names are common Vietnamese names, the numbers
are arbitrary, and nothing corresponds to a real person or account - which is the
only acceptable basis for a demo that will be screen-shared.
"""

from __future__ import annotations

import sys

# The Windows console defaults to cp1252, which cannot encode Vietnamese. Now
# that the assistant answers in Vietnamese, printing a reply raised
# UnicodeEncodeError and took the whole test run down - a test suite that dies
# on its own output reports nothing about the code it was meant to check.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parent / "data" / "accounts.json"
SEED = 20260805

# (name, segment, profile) - the profile drives which scenario the customer
# exercises, so the fixture covers the demo rather than being fifteen copies of
# the same happy path.
PEOPLE = [
    ("Nguyen Van An",    "MASS",     "travel_offer"),
    ("Tran Thi Bich",    "MASS",     "dormant_card"),
    ("Le Minh Chau",     "MASS",     "inactive_card"),
    ("Pham Quoc Dung",   "PRIORITY", "high_balance"),
    ("Hoang Thi Ha",     "MASS",     "frozen_card"),
    ("Vu Duc Hieu",      "MASS",     "loan_in_review"),
    ("Dang Thi Lan",     "MASS",     "overdrawn"),
    ("Bui Van Long",     "MASS",     "blocked_and_replaced"),
    ("Do Thi Mai",       "PRIORITY", "multi_card"),
    ("Ngo Thanh Nam",    "MASS",     "new_customer"),
    ("Duong Thi Oanh",   "MASS",     "mortgage_approved"),
    ("Ly Van Phuc",      "MASS",     "dispute_open"),
    ("Trinh Thi Quyen",  "MASS",     "savings_only"),
    ("Cao Van Son",      "PRIORITY", "business_owner"),
    ("Mai Thi Thuy",     "MASS",     "loan_declined"),
]

MERCHANTS = [
    "VINMART SUPERMARKET", "HIGHLANDS COFFEE", "GRAB TRANSPORT", "SHOPEE PAY",
    "ELECTRICITY BILL", "PHARMACITY", "CGV CINEMA", "PETROLIMEX FUEL",
    "THE GIOI DI DONG", "BACH HOA XANH", "MOBILE TOPUP", "WATER UTILITY",
]

LOAN_PRODUCTS = [
    ("Vay mua ô tô", 240_000_000), ("Vay mua nhà", 1_800_000_000),
    ("Vay tiêu dùng", 80_000_000), ("Vay du học", 150_000_000),
]


def build() -> dict:
    rng = random.Random(SEED)
    today = date(2026, 8, 5)

    used_credentials: set[tuple[str, str]] = set()
    customers = []

    for index, (name, segment, profile) in enumerate(PEOPLE):
        customer_id = f"CUS-{100301 + index * 7}"

        # Unique pair, checked rather than assumed - two customers sharing a
        # phone/ID pair would make verification ambiguous, and the flow would
        # silently pick whichever row came back first.
        while True:
            phone = f"{rng.randint(1000, 9999)}"
            national = f"{rng.randint(1000, 9999)}"
            if (phone, national) not in used_credentials:
                used_credentials.add((phone, national))
                break

        stem = phone
        checking_id = f"ACC-{stem}-{rng.randint(10, 99)}01"
        savings_id = f"ACC-{stem}-{rng.randint(10, 99)}88"

        accounts = []
        if profile != "savings_only":
            balance = {
                "high_balance": rng.uniform(400_000_000, 900_000_000),
                "overdrawn": rng.uniform(-4_000_000, -200_000),
                "new_customer": rng.uniform(500_000, 3_000_000),
                "business_owner": rng.uniform(200_000_000, 600_000_000),
            }.get(profile, rng.uniform(3_000_000, 60_000_000))
            accounts.append({
                "account_id": checking_id,
                "type": "Tài khoản thanh toán",
                "mask": f"****{checking_id[-4:]}",
                "balance": round(balance, 2),
                # Available trails the balance by any uncleared amount. Equal
                # values everywhere would hide the funds-availability story the
                # knowledge base spends a whole document on.
                "available": round(balance - (rng.choice([0, 0, 100_000, 2_500_000])
                                              if balance > 0 else 0), 2),
                "currency": "VND",
            })
        if profile in ("savings_only", "high_balance", "business_owner",
                       "multi_card", "mortgage_approved"):
            savings = rng.uniform(50_000_000, 800_000_000)
            accounts.append({
                "account_id": savings_id,
                "type": "Tài khoản tiết kiệm",
                "mask": f"****{savings_id[-4:]}",
                "balance": round(savings, 2),
                "available": round(savings, 2),
                "currency": "VND",
            })

        cards = _cards_for(profile, stem, rng,
                           accounts[0]["account_id"] if accounts else None)

        loans = []
        if profile == "loan_in_review":
            product, amount = LOAN_PRODUCTS[0]
            loans.append({
                "application_id": f"LN-2026-{rng.randint(10000, 99999)}",
                "product": product, "amount": amount, "status": "Đang thẩm định",
                "submitted_on": str(today - timedelta(days=7)),
                "note": "Hồ sơ đang ở bộ phận thẩm định. Dự kiến có kết quả trong 5 ngày làm việc.",
            })
        elif profile == "mortgage_approved":
            product, amount = LOAN_PRODUCTS[1]
            loans.append({
                "application_id": f"LN-2026-{rng.randint(10000, 99999)}",
                "product": product, "amount": amount, "status": "Đã phê duyệt",
                "submitted_on": str(today - timedelta(days=34)),
                "note": "Đã phê duyệt. Chờ khách hàng ký hợp đồng và ấn định ngày giải ngân.",
            })
        elif profile == "loan_declined":
            product, amount = LOAN_PRODUCTS[2]
            loans.append({
                "application_id": f"LN-2026-{rng.randint(10000, 99999)}",
                "product": product, "amount": amount, "status": "Không được duyệt",
                # A declined application is a case for a human, not a scripted
                # explanation. The note says what the assistant may state and
                # stops there; reasons for a credit decision are not the
                # assistant's to give.
                "note": "Không được duyệt. Trợ lý không giải thích lý do - "
                        "chuyển khách hàng tới chuyên viên tín dụng.",
                "submitted_on": str(today - timedelta(days=21)),
            })

        transactions = []
        for day in range(rng.randint(4, 8)):
            amount = rng.choice([
                -round(rng.uniform(30_000, 900_000), -3),
                -round(rng.uniform(1_000_000, 4_000_000), -3),
                round(rng.uniform(8_000_000, 25_000_000), -3),
            ])
            transactions.append({
                "date": str(today - timedelta(days=day)),
                "description": ("LUONG THANG" if amount > 0
                                else rng.choice(MERCHANTS)),
                "amount": amount,
            })
        if profile == "dispute_open":
            transactions.insert(0, {
                "date": str(today - timedelta(days=2)),
                "description": "GIAO DICH LA - DA MO TRA SOAT",
                "amount": -2_450_000,
            })

        customers.append({
            "customer_id": customer_id,
            "name": name,
            # Stored, not derived. Vietnamese names run family-middle-given, so
            # `name.split()[0]` greets "Nguyen Van An" as "Nguyen" - the family
            # name, shared with roughly a third of the country. Western names
            # run the other way. No rule in code gets both right, so the
            # fixture states which token to use.
            "given_name": name.split()[-1],
            "segment": segment,
            "profile": profile,
            "dob": str(date(rng.randint(1965, 2001), rng.randint(1, 12),
                            rng.randint(1, 28))),
            "phone_last4": phone,
            "national_id_last4": national,
            "accounts": accounts,
            "cards": cards,
            "loans": loans,
            "transactions": transactions,
        })

    return {"customers": customers}


def _cards_for(profile: str, stem: str, rng: random.Random,
               linked: str | None) -> list[dict]:
    """Cards whose statuses collectively cover every transition worth showing."""
    def card(suffix: str, type_: str, status: str, replaces: str | None = None) -> dict:
        card_id = f"CRD-{suffix}"
        entry = {
            "card_id": card_id, "type": type_, "mask": f"****{suffix}",
            "status": status, "linked_account": linked,
        }
        if replaces:
            entry["replaces"] = replaces
        return entry

    a, b = f"{rng.randint(1000, 9999)}", f"{rng.randint(1000, 9999)}"
    if profile == "dormant_card":
        return [card(a, "ghi nợ", "dormant")]
    if profile == "inactive_card":
        return [card(a, "ghi nợ", "inactive")]
    if profile == "frozen_card":
        return [card(a, "ghi nợ", "frozen"), card(b, "tín dụng", "active")]
    if profile == "blocked_and_replaced":
        return [card(a, "ghi nợ", "blocked"),
                card(b, "ghi nợ", "inactive", replaces=f"CRD-{a}")]
    if profile in ("multi_card", "high_balance", "business_owner"):
        return [card(a, "ghi nợ", "active"), card(b, "tín dụng", "active")]
    if profile == "new_customer":
        return [card(a, "ghi nợ", "inactive")]
    if profile == "savings_only":
        return []
    return [card(a, "ghi nợ", "active")]


def check(data: dict) -> list[str]:
    """Structural problems a reader would otherwise find at demo time."""
    problems = []
    seen_credentials: dict[tuple[str, str], str] = {}
    seen_ids: set[str] = set()

    for customer in data["customers"]:
        key = (customer["phone_last4"], customer["national_id_last4"])
        if key in seen_credentials:
            problems.append(
                f"{customer['customer_id']} shares credentials with "
                f"{seen_credentials[key]} - verification would be ambiguous")
        seen_credentials[key] = customer["customer_id"]

        account_ids = {a["account_id"] for a in customer["accounts"]}
        for item in customer["accounts"] + customer["cards"]:
            key_name = "account_id" if "account_id" in item else "card_id"
            if item[key_name] in seen_ids:
                problems.append(f"duplicate id {item[key_name]}")
            seen_ids.add(item[key_name])
            if item["mask"][-4:] != item[key_name][-4:]:
                problems.append(
                    f"{item[key_name]} mask {item['mask']} does not match its id")

        for card in customer["cards"]:
            if card["linked_account"] and card["linked_account"] not in account_ids:
                problems.append(
                    f"{card['card_id']} links to {card['linked_account']}, "
                    f"which {customer['customer_id']} does not hold")
    return problems


def main(argv: list[str]) -> int:
    if "--check" in argv:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    else:
        data = build()

    problems = check(data)
    for problem in problems:
        print(f"  PROBLEM  {problem}")
    if problems:
        return 1

    if "--check" not in argv:
        OUT_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  Wrote {len(data['customers'])} customers to {OUT_PATH}")

    statuses: dict[str, int] = {}
    for customer in data["customers"]:
        for card in customer["cards"]:
            statuses[card["status"]] = statuses.get(card["status"], 0) + 1
    print(f"  Customers : {len(data['customers'])}")
    print(f"  Cards     : {sum(len(c['cards']) for c in data['customers'])} {statuses}")
    print(f"  Accounts  : {sum(len(c['accounts']) for c in data['customers'])}")
    print("  Checks    : passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
