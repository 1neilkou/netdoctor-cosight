"""Build compact, structured context views for actor execution."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath
from typing import Any


PATH_RE = re.compile(
    r"([A-Za-z]:\\[^\s\"'<>|]+|/[^\s\"'<>|]+|[\w./\\-]+\.(?:txt|md|json|csv|html|pdf|docx|xlsx|png|jpg|jpeg|py))"
)
KEY_VALUE_RE = re.compile(
    r"(?i)(final answer\s*:\s*.{1,120}|answer\s*:\s*.{1,120}|[\w ./'\"-]{1,50}\s*=\s*.{1,80}|\b\d+(?:\.\d+)?\s*(?:%|percent|km|miles?|years?|days?|hours?|minutes?|seconds?|kg|g|lb|usd|dollars?)\b)"
)


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return []


def _safe_dict(value: Any) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int | None = None) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _step(plan: Any, step_index: int) -> str:
    steps = _safe_list(getattr(plan, "steps", []))
    return steps[step_index] if 0 <= step_index < len(steps) else ""


def _status(plan: Any, step_index: int) -> str:
    step = _step(plan, step_index)
    return str(_safe_dict(getattr(plan, "step_statuses", {})).get(step, ""))


def _note(plan: Any, step_index: int) -> str:
    step = _step(plan, step_index)
    return str(_safe_dict(getattr(plan, "step_notes", {})).get(step, "") or "")


def _direct_dependencies(plan: Any, step_index: int) -> list[int]:
    deps = getattr(plan, "dependencies", None)
    raw: Any = []
    if isinstance(deps, dict):
        raw = deps.get(step_index, deps.get(str(step_index), []))
    elif isinstance(deps, (list, tuple, set)):
        raw = deps
    result: list[int] = []
    for item in _safe_list(raw):
        try:
            dep_index = int(item)
        except (TypeError, ValueError):
            continue
        if dep_index >= 0 and dep_index not in result:
            result.append(dep_index)
    return result


def _key_values_from_text(text: str, limit: int = 5) -> list[str]:
    values: list[str] = []
    for match in KEY_VALUE_RE.finditer(text or ""):
        value = _text(match.group(1), 120)
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    for match in PATH_RE.finditer(text or ""):
        path = _text(match.group(1).rstrip(".,);]"), 200)
        if path and path not in paths:
            paths.append(path)
    return paths


def _compact_path(path: Any) -> str:
    text = _text(path, 200)
    if not text:
        return ""
    try:
        return PureWindowsPath(text).name if "\\" in text and PureWindowsPath(text).name else text
    except Exception:
        return text


def _artifact_refs(plan: Any, indices: list[int], max_paths: int = 10) -> list[str]:
    refs: list[str] = []
    steps = _safe_list(getattr(plan, "steps", []))
    step_files = _safe_dict(getattr(plan, "step_files", {}))
    tool_calls = _safe_dict(getattr(plan, "step_tool_calls", {}))
    workspace = getattr(plan, "work_space_path", "") or getattr(plan, "workspace_path", "")
    if workspace:
        refs.append(_text(workspace, 200))

    for idx in indices:
        step = steps[idx] if 0 <= idx < len(steps) else ""
        candidates: list[Any] = [step_files.get(step, "")]
        for call in _safe_list(tool_calls.get(step, [])):
            if isinstance(call, dict):
                candidates.extend([call.get("tool_args", ""), call.get("tool_name", "")])
        for candidate in candidates:
            for path in _paths_from_text(str(candidate or "")):
                compact = _compact_path(path)
                if compact and compact not in refs:
                    refs.append(compact)
                if len(refs) >= max_paths:
                    return refs[:max_paths]
    return refs[:max_paths]


def _step_summary(plan: Any, step_index: int, summary_limit: int) -> dict[str, Any]:
    note = _note(plan, step_index)
    return {
        "step_index": step_index,
        "status": _status(plan, step_index),
        "summary": _text(note, summary_limit),
        "key_values": _key_values_from_text(note),
        "artifact_refs": _paths_from_text(note)[:5],
    }


def build_actor_view(
    plan: Any,
    step_index: int,
    max_recent_steps: int = 1,
    max_summary_chars: int = 500,
    max_recent_chars: int = 300,
    include_compact_overview: bool = True,
    include_key_values: bool = True,
    include_artifact_refs: bool = True,
) -> dict[str, Any]:
    """Return a compact context view for one actor step.

    This intentionally avoids full ``Plan.format()``, full step notes, and full
    tool outputs. All Plan fields are accessed defensively.
    """
    steps = _safe_list(getattr(plan, "steps", []))
    statuses = _safe_dict(getattr(plan, "step_statuses", {}))
    tool_calls = _safe_dict(getattr(plan, "step_tool_calls", {}))

    dep_indices = [idx for idx in _direct_dependencies(plan, step_index) if 0 <= idx < len(steps)]
    dep_set = set(dep_indices)
    recent_indices = [
        idx
        for idx, step in enumerate(steps[: max(step_index, 0)])
        if statuses.get(step) == "completed" and idx not in dep_set
    ]
    if max_recent_steps <= 0:
        recent_indices = []
    else:
        recent_indices = recent_indices[-max_recent_steps:]
    related_indices = dep_indices + [idx for idx in recent_indices if idx not in dep_set]

    compact_plan_overview = []
    if include_compact_overview:
        compact_plan_overview = [
            {
                "step_index": idx,
                "status": str(statuses.get(step, "")),
                "one_line_summary": _text(step, 120),
            }
            for idx, step in enumerate(steps)
        ]

    key_values: dict[str, list[str]] = {}
    if include_key_values:
        for idx in related_indices:
            values = _key_values_from_text(_note(plan, idx))
            if values:
                key_values[f"step_{idx}"] = values

    artifact_refs = _artifact_refs(plan, related_indices) if include_artifact_refs else []

    tool_call_brief: dict[str, Any] = {}
    for idx in related_indices:
        step = steps[idx] if 0 <= idx < len(steps) else ""
        calls = _safe_list(tool_calls.get(step, []))
        if calls:
            names = []
            for call in calls[:3]:
                if isinstance(call, dict):
                    name = str(call.get("tool_name", "") or "")
                    if name:
                        names.append(name)
            tool_call_brief[f"step_{idx}"] = {
                "tool_call_count": len(calls),
                "tools": names,
            }

    failed_or_blocked_steps = [
        {
            "step_index": idx,
            "status": str(statuses.get(step, "")),
            "reason": _text(_note(plan, idx), 200),
        }
        for idx, step in enumerate(steps)
        if statuses.get(step) in {"failed", "blocked"}
    ]

    return {
        "current_step_index": step_index,
        "current_step": _text(_step(plan, step_index), None),
        "dependency_summaries": [
            _step_summary(plan, idx, max_summary_chars)
            for idx in dep_indices
        ],
        "recent_completed_summaries": [
            _step_summary(plan, idx, max_recent_chars)
            for idx in recent_indices
        ],
        "compact_plan_overview": compact_plan_overview,
        "failed_or_blocked_steps": failed_or_blocked_steps,
        "artifact_refs": artifact_refs,
        "key_values": key_values,
        "tool_call_brief": tool_call_brief,
    }
