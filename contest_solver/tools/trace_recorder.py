import time
from typing import Any


class TraceRecorder:
    """记录解题过程中每一步的思考轨迹。"""

    def __init__(self, question_id: str):
        self.question_id = question_id
        self._steps: list[dict] = []
        self._step_counter = 0

    def record(self, step_name: str, content: Any, step_type: str = "analysis") -> None:
        self._step_counter += 1
        self._steps.append({
            "step": self._step_counter,
            "step_type": step_type,
            "step_name": step_name,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    def get_trace(self) -> list[dict]:
        return list(self._steps)

    def reset(self) -> None:
        self._steps.clear()
        self._step_counter = 0
