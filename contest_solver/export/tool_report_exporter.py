from __future__ import annotations

import json
from pathlib import Path


KEY_QUESTIONS = {
    "Q002": {
        "title": "Q002 简单计算",
        "tool": "calculator_tool",
        "checks": ["total_mbps", "average_mbps", "spread_mbps"],
        "description": "calculator_tool 是否执行，是否输出总和、平均值、差值。",
    },
    "Q005": {
        "title": "Q005 表格排序",
        "tool": "calculator_tool",
        "checks": ["sorted_cells"],
        "description": "calculator_tool 是否执行，是否输出 RSRP 排序结果。",
    },
    "Q007": {
        "title": "Q007 材料问答",
        "tool": "calculator_tool",
        "checks": [
            "handover_success_rate_delta_pct_points",
            "drop_rate_delta_pct_points",
            "daily_complaints_delta",
        ],
        "description": "calculator_tool 是否执行，是否输出优化前后差值。",
    },
    "Q009": {
        "title": "Q009 数据与规则综合",
        "tool": "rule_evaluator",
        "checks": ["triggered_rules"],
        "description": "rule_evaluator 是否执行，是否输出规则触发结果。",
    },
    "Q010": {
        "title": "Q010 复杂规划",
        "tool": "task_planner",
        "checks": ["stage_count", "stage_names"],
        "description": "task_planner 是否执行，是否输出结构化阶段计划。",
    },
}


def export_tool_executor_report(
    results: list,
    metrics: dict,
    output_dir: str = "outputs/verify_tool_executor",
) -> dict:
    """Export ToolExecutor acceptance report and JSON results."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "tool_executor_report.md"
    json_path = out_dir / "tool_executor_results.json"

    export_results = [_collect_result(r) for r in results]
    payload = {
        "metrics": metrics,
        "results": export_results,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    markdown = _build_markdown(export_results, metrics)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    return {
        "report_path": str(report_path),
        "json_path": str(json_path),
    }


def _collect_result(result: dict) -> dict:
    return {
        "question_id": result.get("question_id"),
        "level": result.get("level"),
        "question_type": result.get("question_type"),
        "routed_tools": result.get("routed_tools", result.get("used_tools", [])),
        "executed_tools": result.get("executed_tools", []),
        "failed_tools": result.get("failed_tools", []),
        "tool_results_summary": result.get("tool_results_summary", {}),
        "reasoning_trace": result.get("reasoning_trace", []),
        "answer_source": result.get("answer_source"),
        "final_answer": result.get("final_answer"),
        "grading_result": result.get("grading_result", {}),
        "status": result.get("status"),
    }


def _build_markdown(results: list[dict], metrics: dict) -> str:
    lines: list[str] = []
    lines.append("# Contest Solver ToolExecutor 验收报告")
    lines.append("")
    lines.extend(_section_overall(metrics))
    lines.extend(_section_overview_table(results))
    lines.extend(_section_key_questions(results))
    lines.extend(_section_failed_tools(results))
    lines.extend(_section_issues_and_next_steps(results))
    return "\n".join(lines).rstrip() + "\n"


def _section_overall(metrics: dict) -> list[str]:
    keys = [
        "total_questions",
        "run_success_rate",
        "schema_valid_rate",
        "trace_complete_rate",
        "tool_call_success_rate",
        "expected_tools_coverage_rate",
        "executed_tools_rate",
        "llm_answer_success_rate",
        "partial_rate",
    ]
    lines = ["## 1. 总体结论", ""]
    for key in keys:
        value = metrics.get(key, "N/A")
        if isinstance(value, float):
            value = f"{value:.3f}"
        lines.append(f"- {key}: {value}")
    lines.append("")
    return lines


def _section_overview_table(results: list[dict]) -> list[str]:
    lines = [
        "## 2. 工具执行概览表",
        "",
        "| question_id | question_type | routed_tools | executed_tools | failed_tools | tool_results_summary | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            "| {qid} | {qtype} | {routed} | {executed} | {failed} | {summary} | {status} |".format(
                qid=_cell(r.get("question_id")),
                qtype=_cell(r.get("question_type")),
                routed=_cell(_join(r.get("routed_tools", []))),
                executed=_cell(_join(r.get("executed_tools", []))),
                failed=_cell(_format_failed_tools(r.get("failed_tools", []))),
                summary=_cell(_compact_json(r.get("tool_results_summary", {}), 180)),
                status=_cell(r.get("status")),
            )
        )
    lines.append("")
    return lines


def _section_key_questions(results: list[dict]) -> list[str]:
    by_id = {r.get("question_id"): r for r in results}
    lines = ["## 3. 关键题目检查", ""]
    for qid, spec in KEY_QUESTIONS.items():
        result = by_id.get(qid)
        lines.append(f"### {spec['title']}")
        lines.append("")
        lines.append(spec["description"])
        lines.append("")
        if not result:
            lines.append("- 检查结果：未找到该题结果。")
            lines.append("")
            continue
        tool = spec["tool"]
        executed = tool in result.get("executed_tools", [])
        summary = result.get("tool_results_summary", {}).get(tool, {})
        checks = _check_summary_fields(summary, spec["checks"])
        lines.append(f"- 工具执行：{'是' if executed else '否'}")
        lines.append(f"- 结果摘要：`{_compact_json(summary, 500)}`")
        lines.append(f"- 关键字段覆盖：{checks}")
        lines.append("")
    return lines


def _section_failed_tools(results: list[dict]) -> list[str]:
    failures: list[tuple[str, dict | str]] = []
    for r in results:
        for failure in r.get("failed_tools", []):
            failures.append((str(r.get("question_id")), failure))

    lines = ["## 4. 失败工具列表", ""]
    if not failures:
        lines.append("无")
        lines.append("")
        return lines

    lines.extend(["| question_id | tool | reason |", "|---|---|---|"])
    for qid, failure in failures:
        if isinstance(failure, dict):
            tool = failure.get("tool", "")
            reason = failure.get("reason", "")
        else:
            tool = str(failure)
            reason = ""
        lines.append(f"| {_cell(qid)} | {_cell(tool)} | {_cell(reason)} |")
    lines.append("")
    return lines


def _section_issues_and_next_steps(results: list[dict]) -> list[str]:
    issues: list[str] = []
    for r in results:
        qid = r.get("question_id")
        routed = set(r.get("routed_tools", []))
        executed = set(r.get("executed_tools", []))
        missing = sorted(routed - executed)
        if missing:
            issues.append(f"{qid}: routed_tools 未全部执行：{', '.join(missing)}")
        if routed and not r.get("tool_results_summary"):
            issues.append(f"{qid}: tool_results_summary 为空。")

    by_id = {r.get("question_id"): r for r in results}
    for qid, spec in KEY_QUESTIONS.items():
        r = by_id.get(qid)
        if not r:
            issues.append(f"{qid}: 缺少验收结果。")
            continue
        tool = spec["tool"]
        summary = r.get("tool_results_summary", {}).get(tool, {})
        if tool not in r.get("executed_tools", []) or not summary:
            issues.append(f"{qid}: 缺少 {tool} 的真实工具结果。")

    lines = ["## 5. 当前问题与下一步建议", ""]
    if issues:
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("- 建议优先补齐上述未执行工具或空摘要，再进入 public_eval 扩展验收。")
    else:
        lines.append("ToolExecutor 已初步生效，可进入 public_eval 或 submission exporter 阶段。")
    lines.append("")
    return lines


def _check_summary_fields(summary: dict, checks: list[str]) -> str:
    if not summary:
        return "缺少摘要"
    haystack = json.dumps(summary, ensure_ascii=False)
    missing = [field for field in checks if field not in haystack]
    if not missing:
        return "完整"
    return "缺少 " + ", ".join(missing)


def _format_failed_tools(failed_tools: list) -> str:
    if not failed_tools:
        return "无"
    parts: list[str] = []
    for item in failed_tools:
        if isinstance(item, dict):
            tool = item.get("tool", "")
            reason = item.get("reason", "")
            parts.append(f"{tool}({reason})" if reason else str(tool))
        else:
            parts.append(str(item))
    return "; ".join(parts)


def _join(items: list) -> str:
    return ", ".join(str(item) for item in items) if items else "无"


def _compact_json(value, limit: int) -> str:
    if not value:
        return "{}"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text[: limit - 3] + "..." if len(text) > limit else text


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
