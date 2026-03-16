"""Gate Manager — the core human-in-the-loop mechanism.

A gate is an approval checkpoint. When the agent hits a gate:
1. It publishes a gate request to Redis (visible via the API).
2. It blocks (via Redis BLPOP) until a human submits a decision through the API.
3. The decision is returned to the agent tool, which resumes execution.

This decouples the agent runtime from the approval UI — approvals can come from
a web dashboard, Slack bot, CLI, or any HTTP client.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog

from orchestrator.config.settings import settings
from orchestrator.models.schemas import (
    EventType,
    Gate,
    GateDecision,
    GateStatus,
    GateType,
    ProjectEvent,
)

log = structlog.get_logger("gates")


class GateManager:
    """Manages human-in-the-loop approval gates backed by Redis."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()

    @property
    def redis(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("GateManager not connected. Call connect() first.")
        return self._redis

    # ── Called by the agent's custom tool (blocks until decision) ──

    async def request_approval(
        self,
        project_id: str,
        gate_type: GateType,
        summary: str,
        details: dict | None = None,
    ) -> GateDecision:
        """Create a gate and block until a human decides.

        This is the function that agent tools call. It will block the agent's
        execution until submit_decision() is called via the API.
        """
        gate = Gate(
            project_id=project_id,
            gate_type=gate_type,
            summary=summary,
            details=details or {},
        )

        # Store the gate
        await self.redis.hset(
            f"project:{project_id}:gates",
            gate.gate_id,
            gate.model_dump_json(),
        )

        # Emit event so the UI knows there's a pending gate
        event = ProjectEvent(
            event_type=EventType.GATE_REQUESTED,
            project_id=project_id,
            data=gate.model_dump(mode="json"),
        )
        await self.redis.publish(
            f"project:{project_id}:events",
            event.model_dump_json(),
        )

        log.info(
            "gate_requested",
            project_id=project_id,
            gate_id=gate.gate_id,
            gate_type=gate_type,
            summary=summary,
        )

        # Block until a decision arrives on the gate's channel
        channel = f"gate:{gate.gate_id}:decision"
        deadline = settings.gate_max_wait_seconds
        elapsed = 0

        while elapsed < deadline:
            result = await self.redis.blpop(
                channel, timeout=settings.gate_poll_interval_seconds
            )
            if result is not None:
                _, raw = result
                decision_data = json.loads(raw)
                decision = GateDecision(**decision_data)

                # Update stored gate
                gate.status = decision.decision
                gate.feedback = decision.feedback
                gate.decided_by = decision.decided_by
                gate.decided_at = datetime.now(timezone.utc)
                await self.redis.hset(
                    f"project:{project_id}:gates",
                    gate.gate_id,
                    gate.model_dump_json(),
                )

                # Emit decided event
                decided_event = ProjectEvent(
                    event_type=EventType.GATE_DECIDED,
                    project_id=project_id,
                    data=gate.model_dump(mode="json"),
                )
                await self.redis.publish(
                    f"project:{project_id}:events",
                    decided_event.model_dump_json(),
                )

                log.info(
                    "gate_decided",
                    gate_id=gate.gate_id,
                    decision=decision.decision,
                    decided_by=decision.decided_by,
                )
                return decision

            elapsed += settings.gate_poll_interval_seconds

        # Timed out — treat as rejection
        log.warning("gate_timed_out", gate_id=gate.gate_id)
        return GateDecision(
            decision=GateStatus.REJECTED,
            feedback="Gate timed out waiting for human approval.",
            decided_by="system",
        )

    # ── Called by the API when a human makes a decision ──

    async def submit_decision(
        self, gate_id: str, decision: GateDecision
    ) -> None:
        """Unblock the agent by pushing the decision onto the gate's channel."""
        channel = f"gate:{gate_id}:decision"
        await self.redis.lpush(channel, decision.model_dump_json())
        log.info("gate_decision_submitted", gate_id=gate_id, decision=decision.decision)

    # ── Query helpers ──

    async def get_pending_gates(self, project_id: str) -> list[Gate]:
        raw_gates = await self.redis.hgetall(f"project:{project_id}:gates")
        gates = []
        for raw in raw_gates.values():
            gate = Gate.model_validate_json(raw)
            if gate.status == GateStatus.PENDING:
                gates.append(gate)
        return gates

    async def get_all_gates(self, project_id: str) -> list[Gate]:
        raw_gates = await self.redis.hgetall(f"project:{project_id}:gates")
        return [Gate.model_validate_json(raw) for raw in raw_gates.values()]

    async def get_gate(self, project_id: str, gate_id: str) -> Gate | None:
        raw = await self.redis.hget(f"project:{project_id}:gates", gate_id)
        if raw is None:
            return None
        return Gate.model_validate_json(raw)


# Singleton instance
gate_manager = GateManager()
