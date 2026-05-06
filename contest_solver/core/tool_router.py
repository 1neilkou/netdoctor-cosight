"""
tool_router.py — 工具路由器

公开接口（新）：
    ToolRouter.select_tools(parsed_question: dict) -> list[str]

路由规则：
    - calculator_tool  : 简单计算 / 表格排序 / 数据与规则综合；
                         材料问答或其他题型只有出现计算意图词时才加入。
    - rule_evaluator   : 条件判断 / 数据与规则综合 / 多跳问答；
                         或 constraints/threshold_values 中含比较运算符/触发/告警级别。
    - task_planner     : Level 3 或复杂规划题型。
    - answer_verifier  : Level 2+ 或多跳/数据规则题型。
    - trace_recorder   : Level 2+ 或多步推理题型。
    - answer_formatter : 需要结构化输出的题型（抽取/转换/规划）。
    - question_parser  : 所有含文本理解的题型。
"""


class ToolRouter:

    TOOL_REGISTRY = {
        "question_parser",
        "calculator_tool",
        "rule_evaluator",
        "trace_recorder",
        "answer_verifier",
        "answer_formatter",
        "task_planner",
    }

    # 触发 calculator_tool 的意图词（不含题型直接推断的情况）
    _CALC_INTENTS = {
        "计算", "比较", "排序", "差值", "平均", "总和",
        "变化了", "提升了", "降低了", "减少了", "增加了",
    }

    # 触发 rule_evaluator 的信号词
    _RULE_SIGNALS = {">", "<", "≥", "≤", ">=", "<=", "触发", "告警级别", "规则", "阈值"}

    # 材料问答中不需要 calculator_tool 的情况（仅纯阅读理解）
    _PURE_READING_SIGNALS = {"是什么", "什么是", "有哪些", "请说明"}

    def select_tools(self, parsed_question: dict) -> list[str]:
        """根据解析结果智能选择工具列表，返回有序、去重的工具名列表。

        优先级：
          1. merged.required_capabilities 指定的能力需求
          2. 题型 / 难度级别 的静态规则路由
          3. semantic_parse.suggested_tools 的 LLM/fallback 建议
        """
        qtype            = parsed_question.get("question_type", "")
        level            = parsed_question.get("level", 1)
        constraints      = parsed_question.get("constraints", [])
        threshold_values = parsed_question.get("threshold_values", [])
        text             = parsed_question.get("question", "")

        # 新增：merged / semantic_parse 字段
        merged               = parsed_question.get("merged", {})
        required_capabilities = merged.get("required_capabilities", [])
        semantic_parse       = parsed_question.get("semantic_parse", {})
        suggested_tools      = [
            t for t in semantic_parse.get("suggested_tools", [])
            if t in self.TOOL_REGISTRY
        ]

        tools: list[str] = []

        def add(t: str) -> None:
            if t not in tools:
                tools.append(t)

        constraint_text = " ".join(str(c) for c in constraints)

        # ---- required_capabilities（来自 merged）--------------------
        if "rule_evaluation" in required_capabilities:
            add("rule_evaluator")
        if "calculation" in required_capabilities:
            add("calculator_tool")
        if "planning" in required_capabilities:
            add("task_planner")
        if "multi_hop_reasoning" in required_capabilities:
            add("trace_recorder")

        # ---- question_parser ----------------------------------------
        if qtype in {
            "文本信息抽取", "JSON格式转换", "条件判断",
            "多跳问答", "复杂规划",
        }:
            add("question_parser")

        # ---- calculator_tool ----------------------------------------
        if qtype in {"简单计算", "表格排序", "数据与规则综合"}:
            add("calculator_tool")
        elif qtype == "材料问答":
            if any(w in text for w in self._CALC_INTENTS):
                add("calculator_tool")
        elif qtype not in {"通信常识问答", "文本信息抽取", "JSON格式转换", "复杂规划"}:
            if any(w in text for w in self._CALC_INTENTS):
                add("calculator_tool")

        # ---- rule_evaluator -----------------------------------------
        has_rule_signal = (
            bool(threshold_values)
            or any(sig in constraint_text for sig in self._RULE_SIGNALS)
            or qtype in {"条件判断", "数据与规则综合", "多跳问答"}
        )
        if has_rule_signal:
            add("rule_evaluator")

        # ---- semantic_parse.suggested_tools -------------------------
        for t in suggested_tools:
            add(t)

        # ---- trace_recorder -----------------------------------------
        if level >= 2 or qtype in {"复杂规划", "多跳问答", "数据与规则综合"}:
            add("trace_recorder")

        # ---- answer_verifier ----------------------------------------
        if level >= 2 or qtype in {"多跳问答", "数据与规则综合"}:
            add("answer_verifier")

        # ---- task_planner -------------------------------------------
        if level == 3 or qtype == "复杂规划":
            add("task_planner")

        # ---- answer_formatter ---------------------------------------
        if qtype in {"文本信息抽取", "JSON格式转换", "复杂规划"} or level == 3:
            add("answer_formatter")

        return tools

    def route(self, tool_name: str | None, payload: dict) -> dict:
        """向后兼容：调用指定工具（当前为占位透传）。"""
        if tool_name is None or tool_name not in self.TOOL_REGISTRY:
            return {"status": "skipped", "tool": tool_name, "output": payload}
        return {"status": "ok", "tool": tool_name, "output": payload}

    def available_tools(self) -> list[str]:
        return sorted(self.TOOL_REGISTRY)
