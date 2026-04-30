"""Extract a contest-style answer payload from a Co-Sight Plan object."""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from pprint import pprint
from typing import Any


def _to_text(value: Any) -> str:
    """Convert any Plan field or tool result to printable text."""
    if value is None:
        return ""
    return str(value)


def _snippet(value: Any, limit: int = 240) -> str:
    """Return a compact single-line snippet for a verbose tool result."""
    text = " ".join(_to_text(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _get_plan_result(plan: Any) -> str:
    """Read Plan.result through the public getter when available."""
    if hasattr(plan, "get_plan_result"):
        return _to_text(plan.get_plan_result()).strip()
    return _to_text(getattr(plan, "result", "")).strip()


def _last_completed_step_notes(plan: Any) -> str:
    """Use the notes from the last completed step as a fallback answer."""
    steps = list(getattr(plan, "steps", []) or [])
    statuses = getattr(plan, "step_statuses", {}) or {}
    notes = getattr(plan, "step_notes", {}) or {}

    for description in reversed(steps):
        if statuses.get(description) == "completed":
            step_notes = _to_text(notes.get(description, "")).strip()
            if step_notes:
                return step_notes
    return ""


def extract_answer(plan: Any) -> dict:
    """Extract final answer, source, and per-step summaries from Co-Sight Plan."""
    plan_result = _get_plan_result(plan)
    if plan_result:
        final_answer = plan_result
        answer_source = "plan_result"
    else:
        final_answer = _last_completed_step_notes(plan)
        answer_source = "last_step_notes"

    statuses = getattr(plan, "step_statuses", {}) or {}
    tool_calls_by_step = getattr(plan, "step_tool_calls", {}) or {}
    steps = []

    for index, description in enumerate(getattr(plan, "steps", []) or []):
        raw_tool_calls = tool_calls_by_step.get(description, []) or []
        tool_calls = [
            {
                "tool_name": _to_text(call.get("tool_name", "")),
                "tool_result_snippet": _snippet(call.get("tool_result", "")),
            }
            for call in raw_tool_calls
            if isinstance(call, dict)
        ]
        steps.append(
            {
                "index": index,
                "description": _to_text(description),
                "status": _to_text(statuses.get(description, "")),
                "tool_calls": tool_calls,
            }
        )

    return {
        "final_answer": final_answer,
        "answer_source": answer_source,
        "steps": steps,
    }


def _run_smoke_plan():
    """Run the same single Co-Sight question and return the live Plan object."""
    from test_single import CoSight, build_llm

    question = "法国的首都是哪里？请用中文回答，并说明该城市的人口大约是多少。"
    workspace_path = Path("outputs/test_single_workspace").resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    os.environ["WORKSPACE_PATH"] = str(workspace_path)

    cosight = CoSight(
        build_llm("PLAN"),
        build_llm("ACT"),
        build_llm("TOOL"),
        build_llm("VISION"),
        work_space_path=str(workspace_path),
        message_uuid="extract_answer_plan",
    )
    cosight.execute(question)
    return cosight.plan


if __name__ == "__main__":
    try:
        pprint(extract_answer(_run_smoke_plan()), width=120)
    except Exception:
        traceback.print_exc()
