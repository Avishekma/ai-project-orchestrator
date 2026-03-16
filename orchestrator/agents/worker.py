"""Background worker — runs the Agent SDK for a project.

This module is called as a background task from the API. It:
1. Sets up the workspace (clones the repo).
2. Assembles custom tools and subagents.
3. Runs the orchestrator agent to completion.
4. Streams progress events to Redis for the SSE endpoint.
"""

from __future__ import annotations

import os
import subprocess

import structlog
from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from orchestrator.agents.prompts import (
    CODE_REVIEWER_PROMPT,
    ORCHESTRATOR_SYSTEM_PROMPT,
    TEST_WRITER_PROMPT,
)
from orchestrator.config.settings import settings
from orchestrator.gates.state import state_manager
from orchestrator.models.schemas import EventType, ProjectEvent, ProjectPhase
from orchestrator.tools.registry import create_project_mcp_server

log = structlog.get_logger("worker")


async def run_project(
    project_id: str,
    document: str,
    repo_url: str,
    jira_project_key: str,
    base_branch: str,
    branch_prefix: str,
) -> None:
    """Long-running background task that drives the entire project workflow."""
    import redis.asyncio as aioredis

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    workspace = os.path.join(settings.workspace_base_dir, project_id)

    try:
        # ── 1. Prepare workspace ──
        await state_manager.update_phase(
            project_id, ProjectPhase.INITIALIZING, "Cloning repository"
        )

        os.makedirs(workspace, exist_ok=True)
        _clone_repo(repo_url, workspace, base_branch)

        # ── 2. Build tool server ──
        mcp_server = create_project_mcp_server(project_id)

        # ── 3. Configure agent ──
        options = ClaudeAgentOptions(
            cwd=workspace,
            allowed_tools=[
                "Read",
                "Write",
                "Edit",
                "Bash",
                "Glob",
                "Grep",
                "Agent",
            ],
            permission_mode="bypassPermissions",
            max_turns=settings.max_agent_turns,
            max_budget_usd=settings.max_budget_usd,
            model=settings.default_model,
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            mcp_servers={"orchestrator": mcp_server},
            agents={
                "test-writer": AgentDefinition(
                    description="Writes unit and integration tests for new code.",
                    prompt=TEST_WRITER_PROMPT,
                    tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                ),
                "code-reviewer": AgentDefinition(
                    description="Reviews code for correctness, security, and quality.",
                    prompt=CODE_REVIEWER_PROMPT,
                    tools=["Read", "Glob", "Grep"],
                ),
            },
        )

        # ── 4. Run the agent ──
        prompt = _build_prompt(document, jira_project_key, base_branch, branch_prefix)

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            # Stream agent text to Redis for the SSE endpoint
                            event = ProjectEvent(
                                event_type=EventType.AGENT_MESSAGE,
                                project_id=project_id,
                                data={"text": block.text[:2000]},
                            )
                            await redis.publish(
                                f"project:{project_id}:events",
                                event.model_dump_json(),
                            )

                elif isinstance(message, ResultMessage):
                    final_phase = ProjectPhase.COMPLETED
                    final_msg = message.result or "Project completed successfully."

                    if message.stop_reason == "max_turns":
                        final_phase = ProjectPhase.FAILED
                        final_msg = "Agent hit max turns limit."

                    await state_manager.update_phase(project_id, final_phase, final_msg)

    except Exception as e:
        log.exception("worker_failed", project_id=project_id)
        await state_manager.update_phase(
            project_id,
            ProjectPhase.FAILED,
            f"Worker crashed: {e!s}",
            error=str(e),
        )
    finally:
        await redis.aclose()


def _clone_repo(repo_url: str, workspace: str, base_branch: str) -> None:
    """Clone the repo into the workspace if not already present."""
    if os.path.exists(os.path.join(workspace, ".git")):
        log.info("repo_already_cloned", workspace=workspace)
        # Pull latest
        subprocess.run(
            ["git", "pull", "origin", base_branch],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        return

    subprocess.run(
        ["git", "clone", "--branch", base_branch, repo_url, "."],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    log.info("repo_cloned", repo_url=repo_url, workspace=workspace)


def _build_prompt(
    document: str,
    jira_project_key: str,
    base_branch: str,
    branch_prefix: str,
) -> str:
    """Build the initial prompt for the orchestrator agent."""
    parts = [
        "Execute the full project workflow for the following specification.\n",
        f"Base branch: {base_branch}",
        f"Branch prefix: {branch_prefix}",
    ]

    if jira_project_key:
        parts.append(f"Jira project key: {jira_project_key}")
    else:
        parts.append("Jira is not configured — skip Jira ticket creation.")

    parts.append(f"\n---\n\nPROJECT DOCUMENT:\n\n{document}")

    return "\n".join(parts)
