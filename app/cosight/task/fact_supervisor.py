"""Rule-based supervisor decisions over structured step facts."""

from __future__ import annotations

import re
from collections import deque
from enum import Enum
from typing import Any


class SupervisorAction(Enum):
    CONTINUE = "continue"
    REPLAN = "replan"
    PRUNE = "prune"
    ADD_VERIFY = "add_verify_step"


REQUIRED_FACT_RE = re.compile(
    r"(?:requires?|needs?|need|需要|依赖|使用|根据)\s*(?:fact|facts|字段|事实|信息)?[:：]?\s*([A-Za-z_][\w.-]*)",
    re.IGNORECASE,
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


def _step(plan: Any, step_index: int) -> str:
    steps = _safe_list(getattr(plan, "steps", []))
    return str(steps[step_index]) if 0 <= step_index < len(steps) else ""


def _step_value(mapping: Any, plan: Any, step_index: int, default: Any) -> Any:
    data = _safe_dict(mapping)
    step = _step(plan, step_index)
    return data.get(step, data.get(step_index, data.get(str(step_index), default)))


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_downstream_steps(plan: Any, completed_step_index: int) -> list[int]:
    """Return steps that directly or indirectly depend on completed_step_index."""
    dependencies = _safe_dict(getattr(plan, "dependencies", {}))
    downstream: list[int] = []
    queue: deque[int] = deque([completed_step_index])
    while queue:
        current = queue.popleft()
        for step_index, deps in dependencies.items():
            try:
                candidate = int(step_index)
            except (TypeError, ValueError):
                continue
            dep_indices = []
            for dep in _safe_list(deps):
                try:
                    dep_indices.append(int(dep))
                except (TypeError, ValueError):
                    continue
            if current in dep_indices and candidate not in downstream:
                downstream.append(candidate)
                queue.append(candidate)
    return downstream


def get_dependency_facts(plan: Any, step_index: int) -> list[dict[str, Any]]:
    """Return facts from direct dependency steps only."""
    facts: list[dict[str, Any]] = []
    dependencies = _safe_dict(getattr(plan, "dependencies", {}))
    raw_deps = dependencies.get(step_index, dependencies.get(str(step_index), []))
    step_facts = _safe_dict(getattr(plan, "step_facts", {}))
    for dep in _safe_list(raw_deps):
        try:
            dep_index = int(dep)
        except (TypeError, ValueError):
            continue
        step = _step(plan, dep_index)
        for fact in _safe_list(step_facts.get(step, [])):
            if isinstance(fact, dict):
                facts.append(fact)
    return facts


def get_required_fact_keys(plan: Any, step_index: int) -> set[str]:
    """Best-effort parser for required fact keys from a step description."""
    step_text = _step(plan, step_index)
    keys = {match.group(1).strip() for match in REQUIRED_FACT_RE.finditer(step_text)}
    return {key for key in keys if key}


def _event_count(plan: Any, action: str) -> int:
    events = _safe_list(getattr(plan, "control_events", []))
    return sum(1 for event in events if isinstance(event, dict) and event.get("action") == action)


def _replan_count(plan: Any) -> int:
    value = getattr(plan, "replan_count", None)
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return _event_count(plan, SupervisorAction.REPLAN.value)


def _added_steps_count(plan: Any) -> int:
    value = getattr(plan, "added_steps_count", None)
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return _event_count(plan, SupervisorAction.ADD_VERIFY.value)


def _facts_are_all_unknown_without_evidence(facts: list[Any]) -> bool:
    if not facts:
        return False
    for fact in facts:
        if not isinstance(fact, dict):
            return False
        source = str(fact.get("source", "") or "").strip().lower()
        evidence = str(fact.get("evidence", "") or "").strip()
        if source != "unknown" or evidence:
            return False
    return True


def _downstream_requires_step_facts(plan: Any, completed_step_index: int) -> bool:
    facts = _safe_list(_step_value(getattr(plan, "step_facts", {}), plan, completed_step_index, []))
    fact_keys = {
        str(fact.get("key", "")).strip()
        for fact in facts
        if isinstance(fact, dict) and str(fact.get("key", "")).strip()
    }
    if not fact_keys:
        return False
    for downstream in get_downstream_steps(plan, completed_step_index):
        if get_required_fact_keys(plan, downstream) & fact_keys:
            return True
    return False


def validate_dag(steps: list[Any], dependencies: dict[Any, Any]) -> list[str]:
    """Validate dependency indexes, duplicate deps, self deps, and cycles."""
    errors: list[str] = []
    step_count = len(steps)
    normalized: dict[int, list[int]] = {}

    for raw_step, raw_deps in _safe_dict(dependencies).items():
        try:
            step_index = int(raw_step)
        except (TypeError, ValueError):
            errors.append(f"dependency key is not an integer: {raw_step}")
            continue
        if step_index < 0 or step_index >= step_count:
            errors.append(f"dependency key out of range: {step_index}")
            continue

        deps: list[int] = []
        seen: set[int] = set()
        for raw_dep in _safe_list(raw_deps):
            try:
                dep_index = int(raw_dep)
            except (TypeError, ValueError):
                errors.append(f"dependency for step {step_index} is not an integer: {raw_dep}")
                continue
            if dep_index == step_index:
                errors.append(f"step {step_index} depends on itself")
            if dep_index < 0 or dep_index >= step_count:
                errors.append(f"dependency index out of range: step {step_index} -> {dep_index}")
            if dep_index in seen:
                errors.append(f"duplicate dependency: step {step_index} -> {dep_index}")
            seen.add(dep_index)
            deps.append(dep_index)
        normalized[step_index] = deps

    indegree = {index: 0 for index in range(step_count)}
    edges = {index: [] for index in range(step_count)}
    for step_index, deps in normalized.items():
        for dep_index in deps:
            if 0 <= dep_index < step_count and dep_index != step_index:
                edges[dep_index].append(step_index)
                indegree[step_index] += 1

    queue: deque[int] = deque([index for index, degree in indegree.items() if degree == 0])
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in edges[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if visited != step_count:
        errors.append("dependencies contain a cycle")
    return errors


def supervisor_check(plan: Any, completed_step_index: int) -> SupervisorAction:
    """Apply rule-based control decisions after one step completes."""
    steps = _safe_list(getattr(plan, "steps", []))
    if completed_step_index < 0 or completed_step_index >= len(steps):
        return SupervisorAction.CONTINUE

    statuses = _safe_dict(getattr(plan, "step_statuses", {}))
    completed_step = steps[completed_step_index]
    downstream_steps = get_downstream_steps(plan, completed_step_index)
    blockers = _safe_list(_step_value(getattr(plan, "step_blockers", {}), plan, completed_step_index, []))
    if statuses.get(completed_step) == "blocked" and not blockers:
        blockers = [{"reason": "step_status_blocked"}]

    if blockers:
        _mark_downstream_dependency_blocked(plan, completed_step_index)

    facts = _safe_list(_step_value(getattr(plan, "step_facts", {}), plan, completed_step_index, []))
    final_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if str(fact.get("key", "")).lower() != "final_answer":
            continue
        confidence = _confidence(fact.get("confidence"))
        if confidence is not None and confidence >= 0.85:
            final_facts.append(fact)
    if final_facts:
        return SupervisorAction.PRUNE

    verified_steps = _safe_list(getattr(plan, "verified_steps", []))
    if _replan_count(plan) >= 1:
        return SupervisorAction.CONTINUE
    if len(verified_steps) >= 2:
        return SupervisorAction.CONTINUE
    if _added_steps_count(plan) >= 3:
        return SupervisorAction.CONTINUE

    confidence = _confidence(_step_value(getattr(plan, "step_confidence", {}), plan, completed_step_index, None))
    if (
        confidence is not None
        and confidence < 0.5
        and _facts_are_all_unknown_without_evidence(facts)
        and len(downstream_steps) >= 2
        and completed_step_index not in verified_steps
    ):
        return SupervisorAction.ADD_VERIFY

    if (
        blockers
        and _downstream_requires_step_facts(plan, completed_step_index)
        and _replan_count(plan) < 1
    ):
        return SupervisorAction.REPLAN

    return SupervisorAction.CONTINUE


def _mark_downstream_dependency_blocked(plan: Any, completed_step_index: int) -> list[int]:
    """Mark not-started downstream steps as dependency_blocked."""
    steps = _safe_list(getattr(plan, "steps", []))
    statuses = _safe_dict(getattr(plan, "step_statuses", {}))
    blocked: list[int] = []
    for index in get_downstream_steps(plan, completed_step_index):
        if 0 <= index < len(steps) and statuses.get(steps[index]) == "not_started":
            statuses[steps[index]] = "dependency_blocked"
            blocked.append(index)
    if blocked and hasattr(plan, "control_events"):
        plan.control_events.append({
            "action": "dependency_blocked",
            "step_index": completed_step_index,
            "blocked_steps": blocked,
        })
    return blocked


def build_supervisor_event(plan: Any, completed_step_index: int, action: SupervisorAction) -> dict[str, Any]:
    """Create a serializable event record for metrics and debugging."""
    missing: dict[str, list[str]] = {}
    for next_step in plan.get_ready_steps() if hasattr(plan, "get_ready_steps") else []:
        required = get_required_fact_keys(plan, next_step)
        available = {
            str(fact.get("key", ""))
            for fact in get_dependency_facts(plan, next_step)
            if isinstance(fact, dict)
        }
        if required - available:
            missing[str(next_step)] = sorted(required - available)

    return {
        "action": action.value,
        "step_index": completed_step_index,
        "downstream_steps": get_downstream_steps(plan, completed_step_index),
        "blockers": _safe_list(_step_value(getattr(plan, "step_blockers", {}), plan, completed_step_index, []))[:5],
        "missing_facts": missing,
    }


def build_replan_context(plan: Any, event: dict[str, Any]) -> str:
    """Compact context for planner replanning after supervisor intervention."""
    steps = _safe_list(getattr(plan, "steps", []))
    statuses = _safe_dict(getattr(plan, "step_statuses", {}))
    blockers = _safe_dict(getattr(plan, "step_blockers", {}))
    overview = []
    for index, step in enumerate(steps):
        overview.append({
            "step_index": index,
            "status": statuses.get(step, "not_started"),
            "step": str(step)[:180],
        })
    return (
        "Supervisor requested replanning.\n"
        f"Plan overview: {overview}\n"
        f"Blockers: {blockers}\n"
        f"Missing facts: {event.get('missing_facts', {})}\n"
        "When updating the plan, keep completed steps, add only necessary recovery steps, "
        "or mark blocked downstream work as skipped when it is no longer needed."
    )


def mark_pruned_steps(plan: Any, reason: str) -> list[int]:
    """Mark remaining not_started steps as skipped."""
    steps = _safe_list(getattr(plan, "steps", []))
    statuses = _safe_dict(getattr(plan, "step_statuses", {}))
    pruned: list[int] = []
    for index, step in enumerate(steps):
        if statuses.get(step) == "not_started":
            statuses[step] = "skipped"
            pruned.append(index)
    if hasattr(plan, "control_events"):
        plan.control_events.append({
            "action": SupervisorAction.PRUNE.value,
            "reason": reason,
            "pruned_steps": pruned,
        })
    return pruned


def add_verify_step(plan: Any, completed_step_index: int) -> int | None:
    """Append one verification step that depends on the low-confidence step."""
    steps = _safe_list(getattr(plan, "steps", []))
    if completed_step_index < 0 or completed_step_index >= len(steps):
        return None
    verified_steps = _safe_list(getattr(plan, "verified_steps", []))
    if completed_step_index in verified_steps:
        return None
    verify_step = (
        f"Verify the result of step {completed_step_index}: {str(steps[completed_step_index])[:160]}. "
        "Check the recorded facts, artifacts, and confidence before continuing."
    )
    if verify_step in steps:
        verified_steps.append(completed_step_index)
        plan.verified_steps = verified_steps
        return steps.index(verify_step)

    new_steps = steps + [verify_step]
    verify_index = len(new_steps) - 1
    new_dependencies = {
        int(step_index): [int(dep) for dep in _safe_list(deps)]
        for step_index, deps in _safe_dict(getattr(plan, "dependencies", {})).items()
    }
    for downstream in get_downstream_steps(plan, completed_step_index):
        deps = new_dependencies.setdefault(downstream, [])
        if verify_index not in deps:
            deps.append(verify_index)
    new_dependencies[verify_index] = [completed_step_index]
    plan.update(steps=new_steps, dependencies=new_dependencies)
    verified_steps.append(completed_step_index)
    plan.verified_steps = verified_steps
    try:
        plan.added_steps_count = _added_steps_count(plan) + 1
    except Exception:
        pass
    return verify_index


def inspect_after_step(plan: Any, step_index: int) -> dict[str, Any]:
    """Backward-compatible wrapper returning the older event dict shape."""
    action = supervisor_check(plan, step_index)
    return build_supervisor_event(plan, step_index, action)
