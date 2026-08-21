"""Deterministic card assignment + evidence-based "why it fits" matching.

Two separate jobs, kept in two functions rather than one, because they are
different kinds of decision:

  * `card_for_segment` - WHICH card a customer gets. Fully determined by
    segment (`data/products.json` maps each of the 4 segments to exactly one
    card) - there is nothing to infer here, so nothing calls a model for it.
  * `explain_fit` - WHY that card fits what the customer just said. The
    client's own framing is "match customer's profile with free-text
    information" (`value_proposition`/`welcome_offers`/`reward_scheme`), not
    a threshold rule. This is answered by search, not inference: each
    welcome-offer/reward line is indexed as its own document
    (`TfidfIndex`, the same machinery `app/retriever.py` uses for the whole
    knowledge base), and the customer's stated interest is scored against
    just that one card's lines. The LLM layer is handed the matched line to
    phrase, never asked to invent why a card is a good fit.

Every recommendation still carries its `disclaimer` as part of the data this
module returns, same as before - no code path ships one without it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .textmodel import TfidfIndex, tokenize

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DISCLAIMERS_PATH = DATA_DIR / "disclaimers.json"
PRODUCTS_PATH = DATA_DIR / "products.json"

# Below this cosine similarity, "matched" would be a stretch - explain_fit
# falls back to the card's value_proposition instead of a weakly related
# reward line that happens to score highest among worse options.
MATCH_FLOOR = 0.08


@dataclass
class MatchedLine:
    text: str
    source: str        # "welcome_offers" | "reward_scheme" | "value_proposition"
    score: float


@dataclass
class CardMatch:
    product_id: str
    product_name: str
    matched_lines: list[MatchedLine]
    disclaimer: str

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id, "product_name": self.product_name,
            "matched_lines": [m.text for m in self.matched_lines],
            "disclaimer": self.disclaimer,
        }


class RulesEngine:
    def __init__(self, disclaimers_path: Path | None = None,
                 products_path: Path | None = None) -> None:
        self.disclaimers_path = disclaimers_path or DISCLAIMERS_PATH
        self.products_path = products_path or PRODUCTS_PATH
        self.reload()

    def reload(self) -> None:
        self.disclaimers = json.loads(self.disclaimers_path.read_text(encoding="utf-8"))["disclaimers"]
        products = json.loads(self.products_path.read_text(encoding="utf-8"))["products"]
        self.products = products
        self.products_by_id = {p["id"]: p for p in products}
        self.product_by_segment = {p["segment"]: p for p in products}

    def card_for_segment(self, segment: str | None) -> dict | None:
        """The one card this segment maps to, or None for an unrecognised
        segment. Deterministic lookup - never a fallback guess."""
        return self.product_by_segment.get(segment)

    def _disclaimer_for(self, product: dict) -> str:
        entry = self.disclaimers.get("credit", self.disclaimers.get("generic", {}))
        return entry.get("text", "")

    def explain_fit(self, product: dict, interest_text: str, top_k: int = 2) -> CardMatch:
        """The lines in `product`'s welcome offers / reward scheme that best
        match what the customer just said, ranked by similarity.

        Scoped to this one card only, never the whole catalogue - segment
        already decided which card, so this step cannot second-guess that
        choice, only explain it.
        """
        lines: list[tuple[str, str]] = (
            [(line, "welcome_offers") for line in product.get("welcome_offers", [])]
            + [(line, "reward_scheme") for line in product.get("reward_scheme", [])]
        )
        disclaimer = self._disclaimer_for(product)

        if not interest_text or not tokenize(interest_text) or not lines:
            return CardMatch(
                product_id=product["id"], product_name=product["name"],
                matched_lines=[MatchedLine(product["value_proposition"], "value_proposition", 0.0)],
                disclaimer=disclaimer,
            )

        index = TfidfIndex([line for line, _ in lines])
        scores = index.similarities(interest_text)
        ranked = sorted(zip(lines, scores), key=lambda pair: -pair[1])

        matched = [MatchedLine(text=line, source=source, score=round(score, 3))
                  for (line, source), score in ranked[:top_k] if score >= MATCH_FLOOR]
        if not matched:
            matched = [MatchedLine(product["value_proposition"], "value_proposition", 0.0)]

        return CardMatch(product_id=product["id"], product_name=product["name"],
                         matched_lines=matched, disclaimer=disclaimer)

    # Every product name here starts with "Thẻ " - a customer saying
    # "Premier" or "thẻ Premier" is naming the same card either way, so
    # matching drops the generic prefix before comparing.
    _GENERIC_PREFIXES = ("Thẻ",)

    def _distinguishing(self, name: str) -> str:
        for prefix in self._GENERIC_PREFIXES:
            if name.startswith(prefix):
                return name[len(prefix):].strip()
        return name

    def mentions(self, text: str) -> list[dict]:
        """Every product named in free text, in the order they appear.

        Deliberately simple (not TF-IDF) - matched on the full name or its
        distinguishing part (name minus "Thẻ"), whichever is present. A
        wrong match here is visible immediately in the reply, unlike a wrong
        match in intent classification.
        """
        lowered = text.lower()
        found = []
        for product in self.products:
            name = product["name"]
            distinguishing = self._distinguishing(name)
            if name.lower() in lowered or (
                len(distinguishing) >= 3 and distinguishing.lower() in lowered):
                found.append(product)
        return found

    def find_product(self, text: str) -> dict | None:
        """The first product named in free text, or None."""
        found = self.mentions(text)
        return found[0] if found else None

    def recommend_all(self, interest_text: str) -> dict | None:
        """Best-matching card across the entire catalogue for a stated interest.

        Used for unverified customers (Scenario 1 – Customer Service) where no
        segment is known.  Returns {"card": product_dict, "match": CardMatch}
        or None when no product scores above MATCH_FLOOR.
        """
        result = self._recommend_for(interest_text)
        if result is not None:
            return result
        # Long sentences dilute TF-IDF (extra words like "thẻ nào phù hợp với tôi"
        # lower cosine similarity below the floor). Fall back to individual content
        # words so "tôi thích chơi golf, thẻ nào phù hợp?" still matches Infinite.
        for token in tokenize(interest_text):
            result = self._recommend_for(token)
            if result is not None:
                return result
        return None

    def _recommend_for(self, interest_text: str) -> dict | None:
        best_card = None
        best_match = None
        best_score = -1.0
        for product in self.products:
            match = self.explain_fit(product, interest_text)
            score = max((m.score for m in match.matched_lines), default=0.0)
            if score > best_score:
                best_score = score
                best_card = product
                best_match = match
        if best_card is None or best_score < MATCH_FLOOR:
            return None
        return {"card": best_card, "match": best_match}


ENGINE = RulesEngine()
