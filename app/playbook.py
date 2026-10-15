"""Cross-sell sales playbook, injected into the generative prompt.

Feeds the credit-card cross-sell script (read from `demo_tutorial.md`) to the
model as *conversational guidance* - the model fuzzy-matches customer needs to
the right card; off-script questions are handled normally. Facts still come only
from KB passages. Applies to both chat and voice call (both go through
`llm.base.build_answer_request` / `build_general_request`).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PLAYBOOK_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "data" / "cross_sell_playbook.md",
    Path(__file__).resolve().parent.parent.parent / "demo_tutorial.md",
)


def _playbook_path() -> Path | None:
    """First existing candidate, or None if the script file is absent."""
    for path in _PLAYBOOK_CANDIDATES:
        if path.exists():
            return path
    return None


CROSS_SELL_INTENTS = frozenset(
    {
        "cross_sell_interest",
        "card_offers",
        "card_catalogue",
        "reward_inquiry",
        "activate_card",
    }
)


def is_cross_sell_context(intent: str | None) -> bool:
    """True when this turn's intent is a card-shopping / cross-sell one."""
    return bool(intent) and intent in CROSS_SELL_INTENTS


_HEADER = """\
── SỔ TAY TƯ VẤN BÁN CHÉO THẺ TÍN DỤNG ──
Dưới đây là kịch bản hướng dẫn cách DẪN DẮT cuộc tư vấn bán chéo thẻ tín dụng.
Áp dụng khi khách hàng quan tâm mở thẻ, hỏi về thẻ, hoặc bạn đang chào thẻ.

Cách dùng:
- Khi khách nói điều GIỐNG (không cần chính xác từng chữ) một nhu cầu/tình huống \
trong kịch bản, hãy phản hồi theo đúng hướng dẫn tương ứng (đổi dòng thẻ phù hợp, \
xử lý đúng 1 trong 3 nhóm tình huống).
- Khi khách nói điều NGOÀI kịch bản, cứ trả lời bình thường theo các quy tắc phía trên.
- Mọi CON SỐ (phí, hạn mức, tỷ lệ hoàn tiền, điều kiện) vẫn phải lấy từ các đoạn \
văn cơ sở tri thức được cung cấp - kịch bản chỉ quyết định cách dẫn dắt, KHÔNG phải \
nguồn số liệu.

--- NỘI DUNG KỊCH BẢN ---
"""


def _load_raw() -> str:
    """Read the playbook file fresh each call (so edits apply without a restart).

    Never raises: a missing or unreadable file simply means no playbook is
    injected and the assistant answers with its normal grounded behaviour,
    rather than taking down every generated turn.
    """
    path = _playbook_path()
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning(
            "playbook file %s could not be read (%s) - "
            "cross-sell playbook not injected this turn",
            path,
            exc,
        )
        return ""


def prompt_section() -> str:
    """The playbook formatted for appending to a system prompt, or "" if none."""
    raw = _load_raw()
    if not raw:
        return ""
    return f"{_HEADER}{raw}"
