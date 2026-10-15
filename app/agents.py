"""Workforce of AI agents and their skillsets.

The bot used to be one hard-coded pipeline that always ran every flow in
`flows.py`. This module turns that single pipeline into a *managed workforce*:
three agents, each owning one skillset, that an admin can configure without
touching code.

  * A **skill** is one intent from `nlu.py` that maps to a scripted flow.
  * A **skillset** is the bundle of skills one agent is responsible for.
  * An **agent** owns exactly one skillset. The admin can disable a whole
    agent, or tick individual skills off within it.

The four conversational baselines - greeting, smalltalk, goodbye, and handing
off to a human - are not skills anyone configures: every agent must be able to
greet and to escalate, so they are always on and never appear in the admin UI.

Routing decision lives in `agent_for_intent()`: given the intent the classifier
produced and whoever is currently serving the session, it returns the agent
that should handle it - which may be a *different* agent, i.e. a transfer. The
router (`router.py::_route_fresh`) calls this immediately before `flows.handle`.

State persists to `data/agents.json` so an admin's configuration survives a
restart, seeded on first run from the defaults below.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .nlu import PERSONAL_INTENTS

AGENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "agents.json"

BASELINE_SKILLS = frozenset({"greeting", "smalltalk", "goodbye", "human_agent"})

_SKILL_NAMES: dict[str, str] = {
    "balance_inquiry": "Tra cứu số dư",
    "account_summary": "Tổng quan tài khoản",
    "transaction_history": "Lịch sử giao dịch",
    "loan_status": "Tình trạng khoản vay",
    "reward_inquiry": "Tra cứu điểm thưởng",
    "activate_card": "Kích hoạt thẻ",
    "block_card": "Báo mất / khóa thẻ",
    "freeze_card": "Tạm khóa thẻ",
    "unfreeze_card": "Mở khóa thẻ",
    "card_close": "Đóng thẻ",
    "card_limit_adjust": "Điều chỉnh hạn mức",
    "product_faq": "Hỏi đáp sản phẩm",
    "product_comparison": "So sánh sản phẩm",
    "knowledge_query": "Tra cứu thông tin chung",
    "cross_sell_interest": "Tư vấn bán chéo",
    "card_offers": "Ưu đãi thẻ",
}

SKILL_CATALOG: dict[str, dict] = {
    intent: {"display_name": name, "sensitive": intent in PERSONAL_INTENTS}
    for intent, name in _SKILL_NAMES.items()
}

_DEFAULT_AGENTS: list[dict] = [
    {
        "id": "ops",
        "name": "Trợ lý Operation Support",
        "skillset_label": "Operation Support",
        "skills": [
            "balance_inquiry",
            "account_summary",
            "transaction_history",
            "loan_status",
            "reward_inquiry",
            "activate_card",
            "block_card",
            "freeze_card",
            "unfreeze_card",
            "card_close",
            "card_limit_adjust",
        ],
    },
    {
        "id": "qa",
        "name": "Trợ lý Product Q&A",
        "skillset_label": "Product Q&A",
        "skills": ["product_faq", "product_comparison", "knowledge_query"],
    },
    {
        "id": "xsell",
        "name": "Trợ lý Cross-sell",
        "skillset_label": "Cross-sell",
        "skills": ["cross_sell_interest", "card_offers"],
    },
]


@dataclass
class Agent:
    id: str
    name: str
    skillset_label: str
    default_skills: tuple[str, ...]
    enabled: bool = True
    enabled_skills: set[str] = field(default_factory=set)

    def can(self, intent: str) -> bool:
        """Whether this agent is live and currently allowed to run `intent`."""
        return self.enabled and intent in self.enabled_skills

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "skillset_label": self.skillset_label,
            "enabled": self.enabled,
            "skills": [
                {
                    "id": s,
                    "display_name": SKILL_CATALOG[s]["display_name"],
                    "sensitive": SKILL_CATALOG[s]["sensitive"],
                    "on": s in self.enabled_skills,
                }
                for s in self.default_skills
            ],
        }


class AgentStore:
    """In-memory workforce with a JSON file behind it.

    Mirrors the persistence style of campaigns.py/demo_script.py: read the file
    if present, otherwise seed from `_DEFAULT_AGENTS` and write it. Every
    mutation is taken under a lock and flushed to disk immediately, because the
    admin edits it live while the router reads it on every turn.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or AGENTS_PATH
        self._lock = threading.Lock()
        self._agents: dict[str, Agent] = {}
        self._load()

    def _load(self) -> None:
        stored: dict[str, dict] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                stored = {a["id"]: a for a in raw.get("agents", [])}
            except (ValueError, KeyError):
                stored = {}

        agents: dict[str, Agent] = {}
        for spec in _DEFAULT_AGENTS:
            defaults = tuple(spec["skills"])
            saved = stored.get(spec["id"], {})
            if "enabled_skills" in saved:
                enabled = {s for s in saved["enabled_skills"] if s in defaults}
                enabled |= {
                    s
                    for s in defaults
                    if s not in set(saved.get("known_skills", defaults))
                }
            else:
                enabled = set(defaults)
            agents[spec["id"]] = Agent(
                id=spec["id"],
                name=spec["name"],
                skillset_label=spec["skillset_label"],
                default_skills=defaults,
                enabled=bool(saved.get("enabled", True)),
                enabled_skills=enabled,
            )
        self._agents = agents
        self._flush_locked()

    def _flush_locked(self) -> None:
        data = {
            "agents": [
                {
                    "id": a.id,
                    "enabled": a.enabled,
                    "enabled_skills": sorted(a.enabled_skills),
                    "known_skills": list(a.default_skills),
                }
                for a in self._agents.values()
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list(self) -> list[Agent]:
        with self._lock:
            return list(self._agents.values())

    def get(self, agent_id: str) -> Agent | None:
        with self._lock:
            return self._agents.get(agent_id)

    def agent_for_intent(
        self, intent: str, current_agent_id: str | None
    ) -> Agent | None:
        """Which agent should handle `intent` next, or None if no one can.

        Baseline intents stay with whoever is already serving the session (or
        the first enabled agent when none is), so "cảm ơn" never triggers a
        pointless transfer. A business intent stays put if the current agent
        can do it, otherwise moves to the first enabled agent that can - that
        move is the transfer the router announces. None means every agent that
        could own this skill has it switched off: the skill is off bank-wide.
        """
        with self._lock:
            current = self._agents.get(current_agent_id) if current_agent_id else None

            if intent in BASELINE_SKILLS:
                if current and current.enabled:
                    return current
                return next((a for a in self._agents.values() if a.enabled), None)

            if current and current.can(intent):
                return current
            return next((a for a in self._agents.values() if a.can(intent)), None)

    def update(
        self,
        agent_id: str,
        *,
        enabled: bool | None = None,
        enabled_skills: list[str] | None = None,
    ) -> Agent | None:
        """Apply an admin edit. Unknown skills and baselines are ignored, so a
        malformed or hostile payload can only ever toggle real, listed skills."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return None
            if enabled is not None:
                agent.enabled = bool(enabled)
            if enabled_skills is not None:
                allowed = set(agent.default_skills)
                agent.enabled_skills = {s for s in enabled_skills if s in allowed}
            self._flush_locked()
            return agent


store = AgentStore()
