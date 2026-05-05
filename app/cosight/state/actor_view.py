"""Build compact structured context views for actor execution."""

from __future__ import annotations

import json
import re
from typing import Any

from app.common.logger_util import logger


PATH_RE = re.compile(
    r"([A-Za-z]:\\[^\s\"'<>|]+|/[^\s\"'<>|]+|[\w./\\-]+\.(?:txt|md|json|csv|html|pdf|docx|xlsx|png|jpg|jpeg|py))"
)

_ASSERT_RUNS = 0
_MAX_ASSERT_RUNS = 10


def token_count(value: Any) -> int:
    """Cheap token estimate used only for guardrail assertions."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return max(1, len(text) // 4)


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
    return text[:limit]


def _step(plan: Any, step_index: int) -> str:
    steps = _safe_list(getattr(plan, "steps", []))
    return str(steps[step_index]) if 0 <= step_index < len(steps) else ""


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


def _paths_from_text(text: Any) -> list[str]:
    paths: list[str] = []
    for match in PATH_RE.finditer(str(text or "")):
        path = _text(match.group(1).rstrip(".,);]"), 200)
        if path and path not in paths:
            paths.append(path)
    return paths


def _compact_path(path: Any) -> str:
    return _text(path, 200)


def _facts_for_step(plan: Any, step_index: int) -> list[dict[str, Any]]:
    step = _step(plan, step_index)
    raw_facts = _safe_list(_safe_dict(getattr(plan, "step_facts", {})).get(step, []))
    facts: list[dict[str, Any]] = []
    for fact in raw_facts:
        if not isinstance(fact, dict):
            continue
        try:
            confidence = fact.get("confidence")
            confidence_value = None if confidence is None else float(confidence)
        except (TypeError, ValueError):
            confidence_value = None
        if confidence_value is not None and confidence_value < 0.5:
            continue

        source = _text(fact.get("source", ""), 160)
        evidence = _text(fact.get("evidence", ""), 240)
        if source.lower() == "unknown" and not evidence:
            continue

        key = _text(fact.get("key", ""), 120)
        value = _text(fact.get("value", ""), 300)
        if not key or not value:
            continue
        facts.append({
            "step_index": step_index,
            "key": key,
            "value": value,
            "source": source,
            "confidence": confidence_value,
            "evidence": evidence,
        })
    return facts


def _step_notes_fallback_fact(plan: Any, step_index: int) -> dict[str, Any] | None:
    """Return a dependency fact from raw step notes when structured facts are absent."""
    step = _step(plan, step_index)
    notes = _safe_dict(getattr(plan, "step_notes", {}))
    note_text = _text(notes.get(step, ""), 300)
    if not note_text:
        return None
    raw_note_text = _text(notes.get(step, ""))
    if len(raw_note_text) > 300:
        note_text = f"{note_text}..."
    return {
        "step_index": step_index,
        "key": f"step_{step_index}_result",
        "value": note_text,
        "source": "step_notes_fallback",
        "confidence": 0.7,
        "evidence": "",
    }


def _artifact_refs(plan: Any, dep_indices: list[int], max_paths: int = 10) -> list[str]:
    steps = _safe_list(getattr(plan, "steps", []))
    step_artifacts = _safe_dict(getattr(plan, "step_artifacts", {}))
    step_files = _safe_dict(getattr(plan, "step_files", {}))
    step_tool_calls = _safe_dict(getattr(plan, "step_tool_calls", {}))
    refs: list[str] = []

    def add_path(path: Any) -> None:
        compact = _compact_path(path)
        if compact and compact not in refs and len(refs) < max_paths:
            refs.append(compact)

    for idx in dep_indices:
        step = steps[idx] if 0 <= idx < len(steps) else ""
        for artifact in _safe_list(step_artifacts.get(step, [])):
            add_path(artifact)
        for path in _paths_from_text(step_files.get(step, "")):
            add_path(path)
        for call in _safe_list(step_tool_calls.get(step, [])):
            if isinstance(call, dict):
                for path in _paths_from_text(call.get("tool_args", "")):
                    add_path(path)
        if len(refs) >= max_paths:
            break
    return refs[:max_paths]


def _tool_summary(plan: Any, dep_indices: list[int]) -> dict[str, Any]:
    steps = _safe_list(getattr(plan, "steps", []))
    step_tool_calls = _safe_dict(getattr(plan, "step_tool_calls", {}))
    names: list[str] = []
    count = 0
    for idx in dep_indices:
        step = steps[idx] if 0 <= idx < len(steps) else ""
        calls = _safe_list(step_tool_calls.get(step, []))
        count += len(calls)
        for call in calls:
            if isinstance(call, dict):
                name = str(call.get("tool_name", "") or "")
                if name and name not in names:
                    names.append(name)
    return {
        "tool_call_count": count,
        "tool_names": names[:8],
    }


def _plan_overview(plan: Any) -> list[dict[str, Any]]:
    steps = _safe_list(getattr(plan, "steps", []))
    statuses = _safe_dict(getattr(plan, "step_statuses", {}))
    notes = _safe_dict(getattr(plan, "step_notes", {}))
    overview: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        note = _text(notes.get(step, ""), 80)
        overview.append({
            "step_index": idx,
            "status": str(statuses.get(step, "")),
            "one_line_summary": note or _text(step, 80),
        })
    return overview


def build_actor_view(plan: Any, step_index: int) -> dict[str, Any]:
    """Return a compact actor view for the current DAG step.

    The view intentionally excludes raw tool outputs, full step notes, and
    full historical facts. It only carries direct dependency facts.
    """
    dep_indices = [
        idx
        for idx in _direct_dependencies(plan, step_index)
        if 0 <= idx < len(_safe_list(getattr(plan, "steps", [])))
    ]

    dependency_facts: list[dict[str, Any]] = []
    for idx in dep_indices:
        facts = _facts_for_step(plan, idx)
        if facts:
            dependency_facts.extend(facts)
            continue
        fallback_fact = _step_notes_fallback_fact(plan, idx)
        if fallback_fact:
            dependency_facts.append(fallback_fact)

    actor_view = {
        "current_step": _step(plan, step_index),
        "dependency_facts": dependency_facts,
        "artifact_refs": _artifact_refs(plan, dep_indices),
        "tool_summary": _tool_summary(plan, dep_indices),
        "plan_overview": _plan_overview(plan),
    }

    global _ASSERT_RUNS
    if _ASSERT_RUNS < _MAX_ASSERT_RUNS:
        _ASSERT_RUNS += 1
        steps = _safe_list(getattr(plan, "steps", []))
        notes = _safe_dict(getattr(plan, "step_notes", {}))
        facts = _safe_dict(getattr(plan, "step_facts", {}))
        tool_calls = _safe_dict(getattr(plan, "step_tool_calls", {}))
        has_history = any(
            notes.get(step) or facts.get(step) or tool_calls.get(step)
            for step in steps[:step_index]
        )
        full_history = plan.format() if has_history and hasattr(plan, "format") else ""
        if full_history:
            actor_view_tokens = token_count(actor_view)
            full_history_tokens = token_count(full_history)
            debug = getattr(plan, "actor_view_debug", None)
            if not isinstance(debug, dict):
                debug = {}
                try:
                    setattr(plan, "actor_view_debug", debug)
                except Exception:
                    debug = {}

            if actor_view_tokens >= full_history_tokens:
                logger.warning(
                    "[actor_view] step %s: actor_view (%s tokens) >= full_history (%s tokens), "
                    "compression did not help; continue execution",
                    step_index,
                    actor_view_tokens,
                    full_history_tokens,
                )
                debug[step_index] = {
                    "actor_view_tokens": actor_view_tokens,
                    "full_history_tokens": full_history_tokens,
                    "warning": "no_compression",
                }
            else:
                debug[step_index] = {
                    "actor_view_tokens": actor_view_tokens,
                    "full_history_tokens": full_history_tokens,
                    "warning": "",
                }

    return actor_view
