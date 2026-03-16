"""MCP tools for the agent to report its own progress.

These tools let the agent update the project phase and emit events
that the API streams to clients via SSE.
"""

from __future__ import annotations

import structlog
from claude_agent_sdk import tool

from orchestrator.gates.state import state_manager
from orchestrator.models.schemas import ProjectPhase

log = structlog.get_logger("status_tools")


def create_status_tools(project_id: str) -> list:
    """Create status-reporting tools bound to a project run."""

    @tool(
        "update_project_status",
        "Update the project's current phase and status message for tracking. "
        "Valid phases: planning, implementing, testing, deploying, completed, failed.",
        {
            "phase": str,
            "message": str,
            "stories_total": int,
            "stories_completed": int,
            "current_story": str,
        },
    )
    async def update_project_status(args: dict) -> dict:
        phase_str = args.get("phase", "implementing")
        message = args.get("message", "")

        try:
            phase = ProjectPhase(phase_str)
        except ValueError:
            phase = ProjectPhase.IMPLEMENTING

        kwargs: dict = {}
        if "stories_total" in args:
            kwargs["stories_total"] = args["stories_total"]
        if "stories_completed" in args:
            kwargs["stories_completed"] = args["stories_completed"]
        if "current_story" in args:
            kwargs["current_story"] = args["current_story"]

        await state_manager.update_phase(project_id, phase, message, **kwargs)
        return _ok(f"Status updated: {phase.value} — {message}")

    return [update_project_status]


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}
