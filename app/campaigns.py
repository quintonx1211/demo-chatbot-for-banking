"""Campaign eligibility, read from the overnight batch.

The client's answers set the shape of this precisely: no real-time core banking
query, a daily CRM push, and a tolerated one-day lag. So eligibility is never
computed here - it is *looked up*. The assistant does not decide who should be
offered a card upgrade; the bank's CRM decided that overnight and this module
reads the answer.

That distinction is worth keeping visible, because it is what makes a
cross-selling bot defensible in a bank. A model that infers "you look like
someone who wants a travel card" is making a suitability judgement about a
financial product. Reading a row from a segmentation file is not.

Card activation is redirection only, again per the client: the assistant hands
over a deeplink or an SMS instruction. It never collects an OTP and never calls
a card system.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CAMPAIGN_PATH = Path(__file__).resolve().parent.parent / "data" / "campaigns.json"


@dataclass
class Offer:
    campaign_id: str
    name: str
    type: str            # activation | reactivation | cross_sell
    body: str            # the message shown to the customer
    cta: str
    deeplink: str
    context: dict

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id, "name": self.name,
            "type": self.type, "cta": self.cta, "deeplink": self.deeplink,
            "context": self.context,
        }


class CampaignBook:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CAMPAIGN_PATH
        self.reload()

    def reload(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.generated_at = data.get("generated_at")
        self.source = data.get("source", "unknown")
        self.campaigns: dict = data.get("campaigns", {})
        self._eligibility = {
            row["customer_id"]: row for row in data.get("eligibility", [])
        }

    @property
    def age_hours(self) -> float | None:
        """How stale the batch is. Shown rather than hidden - a one-day lag is
        acceptable to the client, but only if everyone can see it."""
        if not self.generated_at:
            return None
        try:
            stamp = datetime.fromisoformat(self.generated_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - stamp).total_seconds() / 3600

    def offers_for(self, customer_id: str | None) -> list[Offer]:
        if not customer_id:
            return []
        row = self._eligibility.get(customer_id)
        if not row:
            return []

        offers: list[Offer] = []
        for campaign_id in row.get("campaigns", []):
            spec = self.campaigns.get(campaign_id)
            if not spec:
                continue
            offers.append(Offer(
                campaign_id=campaign_id,
                name=spec["name"],
                type=spec.get("type", "cross_sell"),
                body=self._body(spec, row.get("context", {})),
                cta=spec.get("cta", ""),
                deeplink=spec.get("deeplink", ""),
                context=row.get("context", {}),
            ))
        # Service before selling: an unactivated or dormant card is something
        # the customer is already stuck on, and leading with an upgrade offer
        # while their card does not work is how a campaign bot earns complaints.
        order = {"activation": 0, "reactivation": 1, "cross_sell": 2}
        offers.sort(key=lambda o: order.get(o.type, 3))
        return offers

    @staticmethod
    def _body(spec: dict, context: dict) -> str:
        kind = spec.get("type")
        if kind == "activation":
            mask = context.get("card_mask", "thẻ mới của anh/chị")
            return (
                f"Tôi thấy **thẻ {mask}** của anh/chị chưa được kích hoạt - thẻ "
                f"được phát hành ngày {context.get('issued_on', 'gần đây')}.\n\n"
                f"Anh/chị kích hoạt trong ứng dụng, hoặc "
                f"{spec.get('sms_alternative', 'gọi số in trên thẻ')}."
            )
        if kind == "reactivation":
            mask = context.get("card_mask", "thẻ của anh/chị")
            return (
                f"**Thẻ {mask}** của anh/chị chưa phát sinh giao dịch từ "
                f"{context.get('dormant_since', 'a while ago')}, so it's currently "
                f"dormant.\n\n**{spec.get('offer', '')}** if you start using it "
                f"again."
            )
        return f"{spec.get('detail', '')}"

    @property
    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for spec in self.campaigns.values():
            by_type[spec.get("type", "?")] = by_type.get(spec.get("type", "?"), 0) + 1
        age = self.age_hours
        return {
            "campaigns": len(self.campaigns),
            "by_type": by_type,
            "customers_targeted": len(self._eligibility),
            "generated_at": self.generated_at,
            "source": self.source,
            "age_hours": round(age, 1) if age is not None else None,
        }
