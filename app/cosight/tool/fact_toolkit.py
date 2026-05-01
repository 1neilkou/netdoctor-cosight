"""Structured fact recording toolkit for CoSight actor steps."""

from __future__ import annotations

from typing import Any

from app.common.logger_util import logger


class FactToolkit:
    """Write compact step facts into the shared Plan state."""

    def __init__(self, plan: Any):
        self.plan = plan

    def record_facts(
            self,
            step_index: int,
            facts: list[dict[str, Any]] | None = None,
            artifacts: list[str] | None = None,
            blockers: list[str] | None = None,
            confidence: float | None = None,
            **kwargs: Any) -> str:
        if self.plan is None or not hasattr(self.plan, "add_facts"):
            return "No writable plan is available for record_facts."

        if facts is None and isinstance(kwargs.get("fact"), dict):
            facts = [kwargs["fact"]]
        facts = facts or []
        artifacts = artifacts or []
        blockers = blockers or []

        counts = self.plan.add_facts(
            step_index=step_index,
            facts=facts,
            artifacts=artifacts,
            blockers=blockers,
            confidence=confidence,
        )
        logger.info(
            "Recorded facts for step %s: facts=%s artifacts=%s blockers=%s",
            step_index,
            counts.get("facts", 0),
            counts.get("artifacts", 0),
            counts.get("blockers", 0),
        )
        return (
            f"Recorded facts for step {step_index}: "
            f"facts={counts.get('facts', 0)}, "
            f"artifacts={counts.get('artifacts', 0)}, "
            f"blockers={counts.get('blockers', 0)}"
        )
