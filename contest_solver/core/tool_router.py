class ToolRouter:
    """根据任务描述选择合适的工具（占位实现）。"""

    TOOL_REGISTRY = {
        "question_parser",
        "calculator_tool",
        "trace_recorder",
        "answer_verifier",
        "answer_formatter",
    }

    def route(self, tool_name: str | None, payload: dict) -> dict:
        """调用指定工具并返回结果。当前为占位，直接透传 payload。"""
        if tool_name is None or tool_name not in self.TOOL_REGISTRY:
            return {"status": "skipped", "tool": tool_name, "output": payload}
        return {"status": "ok", "tool": tool_name, "output": payload}

    def available_tools(self) -> list[str]:
        return sorted(self.TOOL_REGISTRY)
