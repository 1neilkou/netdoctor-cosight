"""
solver_pipeline.py — 区域赛解题主流程

新接口（模块级函数）：
    solve_question(question_item: dict) -> dict

    完整 pipeline：
        parse_question → select_tools → [evaluate_rules] → final_answer → format → verify

    ⚠️  当前使用 question_item["expected_answer"] 作为 final_answer 的占位实现，
        仅用于本地模拟测试跑通 pipeline 流程。
        正式比赛中不能依赖 expected_answer，需接入 LLM 或规则推理引擎生成答案。

向后兼容：
    SolverPipeline 类（旧接口，保留不变）
"""
from contest_solver.tools.question_parser import QuestionParser, parse_question
from contest_solver.tools.rule_evaluator  import evaluate_rules
from contest_solver.tools.trace_recorder  import TraceRecorder
from contest_solver.tools.answer_formatter import format_answer, AnswerFormatter
from contest_solver.tools.answer_verifier  import verify_answer
from contest_solver.core.tool_router       import ToolRouter

# ---------------------------------------------------------------------------
# 新接口：solve_question
# ---------------------------------------------------------------------------

_router = ToolRouter()


def solve_question(question_item: dict) -> dict:
    """
    对单道题目执行完整的离线解题 pipeline。

    Args:
        question_item: sample_questions.json 中的单条记录，需含 question_id /
                       level / question_type / question / expected_answer 字段。

    Returns:
        format_answer() 输出结构，额外附加 verifier_result 字段。
    """
    qid      = question_item.get("question_id", "UNKNOWN")
    recorder = TraceRecorder(qid)

    try:
        # ----------------------------------------------------------------
        # Step 1 — 解析题目
        # ----------------------------------------------------------------
        parsed = parse_question(question_item)

        recorder.add_step(
            action        = "解析题目",
            tool          = "question_parser",
            input_summary = f"题目ID={qid}  题型={parsed['question_type']}  难度=L{parsed['level']}",
            observation   = {
                "keywords_top5":      parsed["keywords"][:5],
                "metric_values":      parsed["metric_values"][:6],
                "threshold_values":   parsed["threshold_values"][:4],
                "constraints_count":  len(parsed["constraints"]),
            },
        )

        # ----------------------------------------------------------------
        # Step 2 — 工具路由
        # ----------------------------------------------------------------
        selected_tools = _router.select_tools(parsed)

        recorder.add_step(
            action        = "工具路由",
            tool          = "tool_router",
            input_summary = f"题型={parsed['question_type']}  难度=L{parsed['level']}  "
                            f"constraints={len(parsed['constraints'])}条",
            observation   = {"selected_tools": selected_tools},
        )

        # ----------------------------------------------------------------
        # Step 3 — 规则评估（仅当路由结果包含 rule_evaluator）
        # ----------------------------------------------------------------
        rule_result: dict = {}
        if "rule_evaluator" in selected_tools:
            rule_result = evaluate_rules(parsed)

            obs: dict = {
                "rule_findings":   rule_result.get("rule_findings", []),
                "triggered_rules": rule_result.get("triggered_rules", []),
            }
            ev = rule_result.get("rule_evidence", {})
            if "multi_level_threshold" in ev:
                obs["conclusion"] = ev["multi_level_threshold"].get("conclusion", "")

            recorder.add_step(
                action        = "规则评估",
                tool          = "rule_evaluator",
                input_summary = f"threshold_values={parsed['threshold_values'][:3]}",
                observation   = obs,
                status        = "success" if rule_result.get("rule_findings") else "skipped",
            )

        # ----------------------------------------------------------------
        # Step 4 — 生成最终答案
        # ⚠️  占位：使用 expected_answer 模拟推理引擎输出。
        #    正式比赛中此处需替换为 LLM 调用 / 规则推理引擎。
        # ----------------------------------------------------------------
        final_answer: str = question_item.get("expected_answer", "")

        recorder.add_step(
            action        = "生成答案",
            tool          = "[placeholder: expected_answer]",
            input_summary = "⚠️ 占位模式：使用 expected_answer 绕过推理，仅验证 pipeline 流程",
            observation   = (final_answer[:120] + "...") if len(final_answer) > 120 else final_answer,
        )

        # ----------------------------------------------------------------
        # Step 5 — 格式化输出
        # ----------------------------------------------------------------
        confidence = _calc_confidence(parsed["level"], rule_result, final_answer)

        result = format_answer(
            question_id    = qid,
            level          = parsed["level"],
            question_type  = parsed["question_type"],
            final_answer   = final_answer,
            confidence     = confidence,
            reasoning_trace = recorder.get_trace(),
            used_tools     = selected_tools,
            status         = "success",
        )

        # ----------------------------------------------------------------
        # Step 6 — 校验结果结构
        # ----------------------------------------------------------------
        verifier_result = verify_answer(result, question_item)
        result["verifier_result"] = verifier_result

        return result

    except Exception as exc:
        error_result = format_answer(
            question_id   = qid,
            level         = question_item.get("level", 0),
            question_type = question_item.get("question_type", ""),
            final_answer  = "",
            confidence    = 0.0,
            reasoning_trace = recorder.get_trace(),
            used_tools    = [],
            status        = "error",
            error         = f"{type(exc).__name__}: {exc}",
        )
        error_result["verifier_result"] = {
            "is_valid": False, "missing_fields": [], "warnings": [str(exc)], "score": 0.0
        }
        return error_result


def _calc_confidence(level: int, rule_result: dict, final_answer: str) -> float:
    if not final_answer:
        return 0.0
    base = {1: 0.90, 2: 0.85, 3: 0.80}.get(level, 0.80)
    # rule_evaluator 给出明确结论时略微提升置信度
    if rule_result.get("triggered_rules"):
        base = min(1.0, base + 0.03)
    return base


# ---------------------------------------------------------------------------
# 向后兼容：保留 SolverPipeline 类（旧接口，功能不变）
# ---------------------------------------------------------------------------

class SolverPipeline:
    """
    旧版解题流程类（向后兼容）。
    新代码请使用模块级函数 solve_question()。
    """

    def __init__(self):
        self.parser    = QuestionParser()
        self.formatter = AnswerFormatter()

    def solve_from_file(self, path: str) -> list[dict]:
        questions = self.parser.load_from_file(path)
        return [self._solve_one(q) for q in questions]

    def solve_one(self, raw_question: dict) -> dict:
        parsed = self.parser.parse(raw_question)
        return self._solve_one(parsed)

    def _solve_one(self, parsed: dict) -> dict:
        qid      = parsed["question_id"]
        recorder = TraceRecorder(qid)

        recorder.add_step("问题读取", "question_parser",
                          {"question_id": qid, "category": parsed.get("category", "")})
        recorder.add_step("题型识别", None,
                          f"识别为「{parsed.get('category', '未知')}」题型")

        return self.formatter.format(
            question_id    = qid,
            level          = parsed["level"],
            final_answer   = parsed.get("question", "")[:50] + "（旧版 pipeline 占位）",
            reasoning_trace = recorder.get_trace(),
        )
