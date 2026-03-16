from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field  # noqa: I001

# --- Project ---


class ProjectPhase(StrEnum):
    INITIALIZING = "initializing"
    PLANNING = "planning"
    AWAITING_PLAN_REVIEW = "awaiting_plan_review"
    IMPLEMENTING = "implementing"
    AWAITING_PR_REVIEW = "awaiting_pr_review"
    TESTING = "testing"
    AWAITING_DEPLOY_APPROVAL = "awaiting_deploy_approval"
    DEPLOYING = "deploying"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectCreate(BaseModel):
    """Request body to create a new project run."""

    document: str = Field(..., description="The project spec — markdown text, URL, or file path")
    repo_url: str = Field(..., description="Git clone URL for the target repo")
    jira_project_key: str = Field(
        "", description="Jira project key (e.g. PROJ). Empty to skip Jira."
    )
    base_branch: str = Field("main", description="Base branch to create feature branches from")
    branch_prefix: str = Field("feature", description="Prefix for feature branches")


class ProjectStatus(BaseModel):
    """Current state of a project run."""

    project_id: str
    phase: ProjectPhase
    message: str
    created_at: datetime
    updated_at: datetime
    stories_total: int = 0
    stories_completed: int = 0
    current_story: str = ""
    error: str | None = None


# --- Gates (human-in-the-loop) ---


class GateType(StrEnum):
    PLAN_REVIEW = "plan_review"
    PR_REVIEW = "pr_review"
    DEPLOY_APPROVAL = "deploy_approval"
    GENERIC_APPROVAL = "generic_approval"


class GateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class Gate(BaseModel):
    """A human approval gate."""

    gate_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    gate_type: GateType
    summary: str
    details: dict = Field(default_factory=dict)
    status: GateStatus = GateStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    decided_by: str | None = None
    feedback: str = ""


class GateDecision(BaseModel):
    """Request body to approve / reject a gate."""

    decision: GateStatus = Field(
        ..., description="Must be approved, rejected, or changes_requested"
    )
    feedback: str = Field("", description="Optional feedback for the agent")
    decided_by: str = Field("api_user", description="Who made the decision")


# --- Events (SSE) ---


class EventType(StrEnum):
    STATUS_UPDATE = "status_update"
    GATE_REQUESTED = "gate_requested"
    GATE_DECIDED = "gate_decided"
    AGENT_MESSAGE = "agent_message"
    AGENT_TOOL_USE = "agent_tool_use"
    ERROR = "error"


class ProjectEvent(BaseModel):
    """An event emitted during a project run, streamed via SSE."""

    event_type: EventType
    project_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict = Field(default_factory=dict)
