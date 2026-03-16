"""Project state management backed by Redis.

Tracks the current phase, progress, and metadata for each project run.
All state is persisted in Redis so it survives API restarts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog

from orchestrator.config.settings import settings
from orchestrator.models.schemas import (
    EventType,
    ProjectEvent,
    ProjectPhase,
    ProjectStatus,
)

log = structlog.get_logger("state")


class ProjectStateManager:
    """Read/write project status in Redis."""

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
            raise RuntimeError("ProjectStateManager not connected.")
        return self._redis

    async def create_project(self, project_id: str) -> ProjectStatus:
        now = datetime.now(timezone.utc)
        status = ProjectStatus(
            project_id=project_id,
            phase=ProjectPhase.INITIALIZING,
            message="Project created, preparing workspace",
            created_at=now,
            updated_at=now,
        )
        await self.redis.set(
            f"project:{project_id}:status",
            status.model_dump_json(),
        )
        # Track in global project list
        await self.redis.sadd("projects", project_id)
        return status

    async def update_phase(
        self,
        project_id: str,
        phase: ProjectPhase,
        message: str,
        **kwargs: str | int | None,
    ) -> ProjectStatus:
        status = await self.get_status(project_id)
        if status is None:
            raise ValueError(f"Project {project_id} not found")

        status.phase = phase
        status.message = message
        status.updated_at = datetime.now(timezone.utc)

        for key, value in kwargs.items():
            if hasattr(status, key):
                setattr(status, key, value)

        await self.redis.set(
            f"project:{project_id}:status",
            status.model_dump_json(),
        )

        # Emit status event
        event = ProjectEvent(
            event_type=EventType.STATUS_UPDATE,
            project_id=project_id,
            data={"phase": phase.value, "message": message},
        )
        await self.redis.publish(
            f"project:{project_id}:events",
            event.model_dump_json(),
        )

        log.info("phase_updated", project_id=project_id, phase=phase, message=message)
        return status

    async def get_status(self, project_id: str) -> ProjectStatus | None:
        raw = await self.redis.get(f"project:{project_id}:status")
        if raw is None:
            return None
        return ProjectStatus.model_validate_json(raw)

    async def list_projects(self) -> list[str]:
        return list(await self.redis.smembers("projects"))


# Singleton
state_manager = ProjectStateManager()
