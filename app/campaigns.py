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

This is deliberately separate from `rules_engine.py`, which computes
cross-sell matches from a customer's structured attributes at request time.
This module answers a different question: which marketing campaigns has the
CRM already flagged this specific customer for, independent of whether the
rule engine would also suggest the same product.
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
    body: str            # full message for explicit offer listing
    hook: str            # short tagline for proactive callout
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
                hook=spec.get("hook") or spec.get("offer") or spec["name"],
                cta=spec.get("cta", ""),
                deeplink=spec.get("deeplink", ""),
                context=row.get("context", {}),
            ))
        return offers

    @staticmethod
    def _body(spec: dict, context: dict) -> str:
        parts = [f"**{spec['name']}**"]
        body_text = spec.get("offer") or spec.get("detail") or spec.get("hook") or ""
        if body_text:
            parts.append(body_text)
        if spec.get("terms"):
            parts.append(f"_{spec['terms']}_")
        return "\n\n".join(parts)

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
