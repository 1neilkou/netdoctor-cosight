"""
trace_recorder.py — 解题轨迹记录工具

只记录显式操作（工具调用、观察结果、步骤结论），不记录内部思维链。

公开接口：
    TraceRecorder.add_step(action, tool, observation, status, input_summary)
    TraceRecorder.get_trace() -> list[dict]
    TraceRecorder.reset()

每步格式：
    {
        "step": int,
        "action": str,
        "tool": str | None,
        "input_summary": str,
        "observation": str | dict | list,
        "status": "success" | "failed" | "skipped",
    }
"""


class TraceRecorder:

    VALID_STATUSES = {"success", "failed", "skipped"}

    def __init__(self, question_id: str) -> None:
        self.question_id = question_id
        self._steps: list[dict] = []
        self._counter: int = 0

    def add_step(
        self,
        action: str,
        tool: str | None,
        observation: "str | dict | list",
        status: str = "success",
        input_summary: str | None = None,
    ) -> None:
        """
        记录一个解题步骤。

        Args:
            action:        本步骤的操作名称，例如 "解析题目" / "工具路由" / "规则评估"。
            tool:          使用的工具名称，没有则传 None。
            observation:   工具返回结果或本步骤的观察结论（字符串 / dict / list 均可）。
            status:        "success" / "failed" / "skipped"。
            input_summary: 本步骤输入的简要描述（可选）。
        """
        if status not in self.VALID_STATUSES:
            status = "success"
        self._counter += 1
        self._steps.append({
            "step":          self._counter,
            "action":        action,
            "tool":          tool,
            "input_summary": input_summary or "",
            "observation":   observation,
            "status":        status,
        })

    def get_trace(self) -> list[dict]:
        return list(self._steps)

    def reset(self) -> None:
        self._steps.clear()
        self._counter = 0
