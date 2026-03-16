"""MCP tools that create human-in-the-loop gates.

Each tool blocks the agent until a human responds via the API.
Tools are factory-scoped to a project_id so each run is isolated.
"""

from __future__ import annotations

from claude_agent_sdk import tool

from orchestrator.gates.manager import gate_manager
from orchestrator.models.schemas import GateStatus, GateType


def create_gate_tools(project_id: str) -> list:
    """Create gate tools bound to a specific project run."""

    @tool(
        "request_plan_approval",
        "Submit the project plan and stories for human review. "
        "Blocks until the human approves, requests changes, or rejects.",
        {"summary": str, "stories_json": str},
    )
    async def request_plan_approval(args: dict) -> dict:
        decision = await gate_manager.request_approval(
            project_id=project_id,
            gate_type=GateType.PLAN_REVIEW,
            summary=args["summary"],
            details={"stories_json": args["stories_json"]},
        )
        if decision.decision == GateStatus.APPROVED:
            return _ok("APPROVED. Proceed with implementation.")
        return _ok(f"CHANGES_REQUESTED: {decision.feedback}")

    @tool(
        "request_pr_approval",
        "Submit a pull request for human review. "
        "Blocks until the human approves or requests changes.",
        {"story_id": str, "pr_url": str, "summary": str},
    )
    async def request_pr_approval(args: dict) -> dict:
        decision = await gate_manager.request_approval(
            project_id=project_id,
            gate_type=GateType.PR_REVIEW,
            summary=f"PR for {args['story_id']}: {args['summary']}",
            details={"pr_url": args["pr_url"], "story_id": args["story_id"]},
        )
        if decision.decision == GateStatus.APPROVED:
            return _ok("PR APPROVED. Merge and continue.")
        return _ok(f"PR CHANGES REQUESTED: {decision.feedback}")

    @tool(
        "request_deploy_approval",
        "Request approval to deploy the completed project. "
        "Blocks until the human approves or rejects.",
        {"environment": str, "summary": str},
    )
    async def request_deploy_approval(args: dict) -> dict:
        decision = await gate_manager.request_approval(
            project_id=project_id,
            gate_type=GateType.DEPLOY_APPROVAL,
            summary=f"Deploy to {args['environment']}",
            details={
                "environment": args["environment"],
                "summary": args["summary"],
            },
        )
        if decision.decision == GateStatus.APPROVED:
            return _ok("DEPLOY APPROVED. Proceed.")
        return _ok(f"DEPLOY REJECTED: {decision.feedback}")

    return [request_plan_approval, request_pr_approval, request_deploy_approval]


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}
