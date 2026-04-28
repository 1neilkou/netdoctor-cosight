from __future__ import annotations

from contest_solver.core.task_planner import TaskPlanner
from contest_solver.tools.calculator_tool import CalculatorTool
from contest_solver.tools.rule_evaluator import evaluate_rules


def execute_tools(
    question_item: dict,
    parsed_result: dict,
    routed_tools: list,
) -> dict:
    """Execute the tools selected by ToolRouter.

    The executor never reads expected_answer. Tool failures are captured in the
    returned failed_tools list so a single tool cannot crash the pipeline.
    """
    tool_results: dict = {}
    executed_tools: list[str] = []
    failed_tools: list[dict] = []
    evidence: list[str] = []

    def record_success(tool_name: str, result: dict) -> None:
        tool_results[tool_name] = result
        executed_tools.append(tool_name)
        for item in result.get("evidence", []):
            evidence.append(f"{tool_name}: {item}")

    def record_failure(tool_name: str, reason: str, result: dict | None = None) -> None:
        if result is not None:
            tool_results[tool_name] = result
        failed_tools.append({"tool": tool_name, "reason": reason})
        evidence.append(f"{tool_name}: failed - {reason}")

    for tool_name in routed_tools:
        if tool_name == "rule_evaluator":
            try:
                result = evaluate_rules(parsed_result)
                result.setdefault("status", "success")
                result.setdefault("evidence", [])
                if result.get("rule_findings"):
                    result["evidence"].extend(result["rule_findings"])
                record_success(tool_name, result)
            except Exception as exc:
                record_failure(tool_name, f"{type(exc).__name__}: {exc}")

        elif tool_name == "calculator_tool":
            try:
                result = CalculatorTool().execute(question_item, parsed_result)
                if result.get("status") == "success":
                    record_success(tool_name, result)
                else:
                    record_failure(tool_name, result.get("calculation_type", "failed"), result)
            except Exception as exc:
                record_failure(tool_name, f"{type(exc).__name__}: {exc}")

        elif tool_name == "task_planner":
            try:
                result = TaskPlanner().execute(question_item, parsed_result)
                if result.get("status") == "success":
                    record_success(tool_name, result)
                else:
                    record_failure(tool_name, "planning_failed", result)
            except Exception as exc:
                record_failure(tool_name, f"{type(exc).__name__}: {exc}")

    return {
        "tool_results": tool_results,
        "executed_tools": executed_tools,
        "failed_tools": failed_tools,
        "evidence": evidence,
    }


def summarize_tool_results(tool_results: dict) -> dict:
    """Create a compact, stable summary for result output and demo display."""
    summary: dict = {}
    for tool_name, result in tool_results.items():
        if tool_name == "rule_evaluator":
            summary[tool_name] = {
                "status": result.get("status", "success"),
                "findings_count": len(result.get("rule_findings", [])),
                "triggered_rules": result.get("triggered_rules", []),
            }
        elif tool_name == "calculator_tool":
            summary[tool_name] = {
                "status": result.get("status"),
                "calculation_type": result.get("calculation_type"),
                "result": result.get("result", {}),
            }
        elif tool_name == "task_planner":
            summary[tool_name] = {
                "status": result.get("status"),
                "stage_count": len(result.get("stages", [])),
                "stage_names": [s.get("stage", "") for s in result.get("stages", [])],
            }
        else:
            summary[tool_name] = {"status": result.get("status", "unknown")}
    return summary
