"""
solver_pipeline.py — 区域赛解题主流程

新接口（模块级函数）：
    solve_question(question_item, use_llm=False, use_llm_answer=False) -> dict

    完整 pipeline：
        parse_question → select_tools → [evaluate_rules] → final_answer → format → verify

    答案生成策略：
        use_llm_answer=False（默认）：使用 expected_answer 作为占位，仅验证流程完整性。
        use_llm_answer=True         ：调用 llm_answerer 生成真实答案；LLM 失败时 status="partial"。

    ⚠️  expected_answer 只用于 use_llm_answer=False 的占位模式 / 评测对比，不传给 LLM。

向后兼容：
    SolverPipeline 类（旧接口，保留不变）
"""
from contest_solver.tools.question_parser  import QuestionParser, parse_question
from contest_solver.tools.trace_recorder   import TraceRecorder
from contest_solver.tools.answer_formatter import format_answer, AnswerFormatter
from contest_solver.tools.answer_verifier  import verify_answer
from contest_solver.core.tool_router       import ToolRouter
from contest_solver.core.tool_executor     import execute_tools, summarize_tool_results
from contest_solver.eval.semantic_grader   import grade_answer

_router = ToolRouter()

# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def solve_question(
    question_item: dict,
    use_llm: bool = False,
    use_llm_answer: bool = False,
) -> dict:
    """
    对单道题目执行完整解题 pipeline。

    Args:
        question_item:   样本题目记录。
        use_llm:         True 时对 question_parser 启用 LLM 语义解析。
        use_llm_answer:  True 时调用 llm_answerer 生成 final_answer；
                         False（默认）时使用 expected_answer 占位。

    Returns:
        format_answer() 结构，附加字段：
            verifier_result       — 校验结果
            semantic_parse_source — "llm" 或 "fallback"
            answer_source         — "llm" / "fallback" / "placeholder"
            llm_answer_debug      — LLM answerer 的诊断信息（非 llm_answer 模式时为 {}）
    """
    qid      = question_item.get("question_id", "UNKNOWN")
    recorder = TraceRecorder(qid)

    try:
        # ----------------------------------------------------------------
        # Step 1 — 解析题目
        # ----------------------------------------------------------------
        parsed          = parse_question(question_item, use_llm=use_llm)
        semantic_source = parsed.get("semantic_parse", {}).get("source", "fallback")

        recorder.add_step(
            action        = "解析题目",
            tool          = "question_parser",
            input_summary = (
                f"题目ID={qid}  题型={parsed['question_type']}  "
                f"难度=L{parsed['level']}  语义解析={semantic_source}"
            ),
            observation = {
                "keywords_top5":     parsed["keywords"][:5],
                "metric_values":     parsed["metric_values"][:6],
                "threshold_values":  parsed["threshold_values"][:4],
                "constraints_count": len(parsed["constraints"]),
                "semantic_source":   semantic_source,
            },
        )

        # ----------------------------------------------------------------
        # Step 2 — 工具路由
        # ----------------------------------------------------------------
        selected_tools = _router.select_tools(parsed)

        recorder.add_step(
            action        = "工具路由",
            tool          = "tool_router",
            input_summary = (
                f"题型={parsed['question_type']}  难度=L{parsed['level']}  "
                f"constraints={len(parsed['constraints'])}条"
            ),
            observation = {"selected_tools": selected_tools},
        )

        # ----------------------------------------------------------------
        # Step 3 — 真实工具执行
        # ----------------------------------------------------------------
        execution_result = execute_tools(
            question_item = question_item,
            parsed_result = parsed,
            routed_tools  = selected_tools,
        )
        tool_results = execution_result["tool_results"]
        executed_tools = execution_result["executed_tools"]
        failed_tools = execution_result["failed_tools"]
        tool_results_summary = summarize_tool_results(tool_results)
        rule_result: dict = tool_results.get("rule_evaluator", {})

        recorder.add_step(
            action        = "执行路由工具",
            tool          = "tool_executor",
            input_summary = f"routed_tools={selected_tools}",
            observation   = {
                "executed_tools": executed_tools,
                "failed_tools": failed_tools,
                "tool_results_summary": tool_results_summary,
                "evidence": execution_result.get("evidence", [])[:8],
            },
            status        = (
                "success" if executed_tools and not failed_tools
                else ("skipped" if not executed_tools else "failed")
            ),
        )

        # ----------------------------------------------------------------
        # Step 4 — 生成最终答案
        # ----------------------------------------------------------------
        if use_llm_answer:
            from contest_solver.tools.llm_answerer import generate_llm_answer

            answer_result = generate_llm_answer(
                question_item = question_item,
                parsed_result = parsed,
                routed_tools  = selected_tools,
                tool_results  = tool_results,
            )
            final_answer     = answer_result["final_answer"]
            answer_source    = answer_result["answer_source"]
            llm_answer_debug = answer_result["debug"]

            # LLM 自身置信度优先；回退时用规则估算
            confidence = (
                answer_result["confidence"]
                if answer_result["confidence"] > 0
                else _calc_confidence(parsed["level"], rule_result, final_answer)
            )

            pipeline_status = (
                "success" if (answer_source == "llm" and final_answer)
                else "partial"
            )

            recorder.add_step(
                action        = "生成答案",
                tool          = "llm_answerer",
                input_summary = f"use_llm_answer=True  题型={parsed['question_type']}",
                observation   = {
                    "answer_source":   answer_source,
                    "confidence":      round(confidence, 3),
                    "fallback_reason": llm_answer_debug.get("fallback_reason"),
                    "answer_preview":  (final_answer[:80] + "...") if len(final_answer) > 80 else final_answer,
                },
            )
        else:
            # ⚠️ 占位模式：使用 expected_answer，仅用于验证 pipeline 流程
            final_answer     = question_item.get("expected_answer", "")
            answer_source    = "placeholder"
            llm_answer_debug = {}
            confidence       = _calc_confidence(parsed["level"], rule_result, final_answer)
            pipeline_status  = "success"

            recorder.add_step(
                action        = "生成答案",
                tool          = "[placeholder: expected_answer]",
                input_summary = "⚠️ 占位模式：使用 expected_answer 绕过推理，仅验证 pipeline 流程",
                observation   = (
                    (final_answer[:120] + "...") if len(final_answer) > 120 else final_answer
                ),
            )

        # ----------------------------------------------------------------
        # Step 5 — 格式化输出
        # ----------------------------------------------------------------
        result = format_answer(
            question_id     = qid,
            level           = parsed["level"],
            question_type   = parsed["question_type"],
            final_answer    = final_answer,
            confidence      = confidence,
            reasoning_trace = recorder.get_trace(),
            used_tools      = selected_tools,
            status          = pipeline_status,
        )

        # ----------------------------------------------------------------
        # Step 6 — 校验结果结构
        # ----------------------------------------------------------------
        pipeline_executed_tools = list(executed_tools)
        for built_in_tool in (
            "question_parser",
            "trace_recorder",
            "answer_formatter",
            "answer_verifier",
        ):
            if built_in_tool in selected_tools and built_in_tool not in pipeline_executed_tools:
                pipeline_executed_tools.append(built_in_tool)

        verifier_result = verify_answer(result, question_item)
        tool_results_summary = dict(tool_results_summary)
        if "question_parser" in selected_tools and "question_parser" not in tool_results_summary:
            tool_results_summary["question_parser"] = {
                "status": "success",
                "semantic_source": semantic_source,
                "keywords_count": len(parsed.get("keywords", [])),
                "constraints_count": len(parsed.get("constraints", [])),
            }
        if "answer_formatter" in selected_tools and "answer_formatter" not in tool_results_summary:
            tool_results_summary["answer_formatter"] = {
                "status": "success",
                "final_answer_length": len(final_answer or ""),
                "output_status": pipeline_status,
            }
        if "trace_recorder" in selected_tools and "trace_recorder" not in tool_results_summary:
            tool_results_summary["trace_recorder"] = {
                "status": "success",
                "step_count": len(result.get("reasoning_trace", [])),
            }
        if "answer_verifier" in selected_tools and "answer_verifier" not in tool_results_summary:
            tool_results_summary["answer_verifier"] = {
                "status": "success" if verifier_result.get("is_valid") else "warning",
                "is_valid": verifier_result.get("is_valid", False),
                "score": verifier_result.get("score", 0.0),
            }

        result["verifier_result"]       = verifier_result
        result["semantic_parse_source"] = semantic_source
        result["answer_source"]         = answer_source
        result["llm_answer_debug"]      = llm_answer_debug
        result["grading_result"]        = grade_answer(result, question_item)
        result["routed_tools"]          = selected_tools
        result["executed_tools"]        = pipeline_executed_tools
        result["failed_tools"]          = failed_tools
        result["tool_results_summary"]  = tool_results_summary

        return result

    except Exception as exc:
        error_result = format_answer(
            question_id     = qid,
            level           = question_item.get("level", 0),
            question_type   = question_item.get("question_type", ""),
            final_answer    = "",
            confidence      = 0.0,
            reasoning_trace = recorder.get_trace(),
            used_tools      = [],
            status          = "error",
            error           = f"{type(exc).__name__}: {exc}",
        )
        error_result["verifier_result"]       = {
            "is_valid": False, "missing_fields": [], "warnings": [str(exc)], "score": 0.0
        }
        error_result["semantic_parse_source"] = "fallback"
        error_result["answer_source"]         = "placeholder"
        error_result["llm_answer_debug"]      = {}
        error_result["grading_result"]        = {
            "exact_match": False, "normalized_match": False, "semantic_score": 0.0,
            "matched_points": [], "missing_points": [], "grading_method": "error",
        }
        error_result["routed_tools"]          = []
        error_result["executed_tools"]        = []
        error_result["failed_tools"]          = []
        error_result["tool_results_summary"]  = {}
        return error_result


def _calc_confidence(level: int, rule_result: dict, final_answer: str) -> float:
    if not final_answer:
        return 0.0
    base = {1: 0.90, 2: 0.85, 3: 0.80}.get(level, 0.80)
    if rule_result.get("triggered_rules"):
        base = min(1.0, base + 0.03)
    return base


# ---------------------------------------------------------------------------
# 向后兼容：SolverPipeline 类
# ---------------------------------------------------------------------------

class SolverPipeline:
    """旧版解题流程类（向后兼容）。新代码请使用 solve_question()。"""

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
            question_id     = qid,
            level           = parsed["level"],
            final_answer    = parsed.get("question", "")[:50] + "（旧版 pipeline 占位）",
            reasoning_trace = recorder.get_trace(),
        )
