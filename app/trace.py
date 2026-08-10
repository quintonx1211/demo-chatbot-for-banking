"""Decision trace - why a turn took the route it did.

The audit trail already records *what* happened. This records the reasoning:
every gate the turn passed through, what it saw, and which threshold it was
compared against. Two audiences need it for different reasons.

A reviewer needs to answer "why did the assistant say that" months later, and
"the classifier was confident" is not an answer - "intent `block_card` scored
0.91 against a 0.55 threshold, so the scripted flow ran and no model was
involved" is.

A prospect in a demo needs to see that the routing is a set of stated rules
rather than a black box. The single most common question about a hybrid
assistant is "how does it decide?", and pointing at a trace answers it better
than any diagram.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    stage: str          # pending_flow | guardrail | nlu | retrieval | generation | ...
    outcome: str        # what this stage concluded
    detail: str = ""    # the numbers behind it
    decisive: bool = False   # True if this stage is why the turn ended up where it did

    def to_dict(self) -> dict:
        return {"stage": self.stage, "outcome": self.outcome,
                "detail": self.detail, "decisive": self.decisive}


@dataclass
class Trace:
    steps: list[Step] = field(default_factory=list)

    def add(self, stage: str, outcome: str, detail: str = "",
            decisive: bool = False) -> None:
        self.steps.append(Step(stage, outcome, detail, decisive))

    def decide(self, stage: str, outcome: str, detail: str = "") -> None:
        """Record the step that settled the route."""
        self.add(stage, outcome, detail, decisive=True)

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.steps]

    @property
    def summary(self) -> str:
        """One line naming the deciding stage, for the audit note."""
        decisive = next((s for s in reversed(self.steps) if s.decisive), None)
        return f"{decisive.stage}: {decisive.outcome}" if decisive else ""


# Human-readable descriptions of each stage, shown alongside the trace so the
# rule being applied is legible without reading the source.
STAGE_RULES = {
    "pending_flow": "Luồng nghiệp vụ đang dở giữ quyền xử lý lượt này, trừ khi "
                    "khách hàng rõ ràng đã chuyển sang chủ đề khác.",
    "handoff_offer": "Lời mời chuyển chuyên viên đang chờ trả lời được xử lý "
                     "trước khi phân loại bất kỳ điều gì khác.",
    "guardrail": "Chủ đề thuộc diện quản lý bị từ chối tại đây, trước khi bất "
                 "kỳ model nào nhìn thấy nội dung câu hỏi.",
    "campaign": "Chiến dịch khách hàng đủ điều kiện, đọc từ tệp trích xuất "
                "hằng đêm. Tất định - không có model tham gia.",
    "nlu": "Độ tin cậy ý định từ 0.55 trở lên sẽ chạy luồng nghiệp vụ đọc "
           "thẳng hồ sơ. Dưới mức đó, câu hỏi đi sang kho tri thức.",
    "retrieval": "Đoạn tài liệu phải vượt ngưỡng liên quan. Không đoạn nào "
                 "vượt nghĩa là không có nguồn đã thẩm định, và trợ lý đề nghị "
                 "chuyển chuyên viên thay vì đoán.",
    "generation": "Model chỉ được dùng các đoạn đã truy xuất. Câu trả lời sau "
                  "đó được chấm điểm dựa trên chính các đoạn đó; dưới 0.55 sẽ "
                  "bị loại bỏ.",
    "escalation": "Đã chuyển cho chuyên viên, kèm toàn bộ nội dung hội thoại.",
    "privacy": "Dữ liệu nhạy cảm được che trước khi truy xuất và trước khi nội "
               "dung rời khỏi hệ thống tới nhà cung cấp model.",
    "raw_mode": "Công tắc demo, chỉ bật cho hội thoại này: định tuyến, "
                "guardrail, truy xuất và kiểm tra dẫn nguồn đều bị bỏ qua - "
                "model trả lời chỉ dựa trên lịch sử hội thoại.",
}
