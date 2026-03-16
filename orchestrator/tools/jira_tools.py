"""MCP tools for Jira integration.

Creates epics and stories via the Jira REST API.
Falls back to a mock/log mode when Jira credentials are not configured.
"""

from __future__ import annotations

import json

import httpx
import structlog
from claude_agent_sdk import tool

from orchestrator.config.settings import settings

log = structlog.get_logger("jira")


def create_jira_tools(project_id: str) -> list:
    """Create Jira tools bound to a project run."""

    @tool(
        "create_jira_epic",
        "Create a Jira epic with child stories. "
        "Input: project_key, epic_summary, stories (JSON array of objects with "
        "summary, description, and acceptance_criteria fields).",
        {"project_key": str, "epic_summary": str, "stories": str},
    )
    async def create_jira_epic(args: dict) -> dict:
        project_key = args["project_key"]
        epic_summary = args["epic_summary"]
        stories_raw = args["stories"]

        try:
            stories = json.loads(stories_raw)
        except json.JSONDecodeError:
            return _err("Invalid JSON in stories field.")

        if not settings.jira_base_url or not settings.jira_api_token:
            # Mock mode — log and return fake IDs
            log.warning("jira_mock_mode", reason="Jira credentials not configured")
            created = []
            for i, story in enumerate(stories, start=1):
                ticket_id = f"{project_key}-{100 + i}"
                created.append(f"{ticket_id}: {story.get('summary', 'Untitled')}")
            epic_id = f"{project_key}-100"
            result = f"[MOCK] Created epic {epic_id}: {epic_summary}\nStories:\n" + "\n".join(
                f"  - {c}" for c in created
            )
            return _ok(result)

        # Real Jira API calls
        auth = (settings.jira_email, settings.jira_api_token)
        base = settings.jira_base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}

        async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30) as client:
            # Create epic
            epic_resp = await client.post(
                f"{base}/rest/api/3/issue",
                json={
                    "fields": {
                        "project": {"key": project_key},
                        "summary": epic_summary,
                        "issuetype": {"name": "Epic"},
                    }
                },
            )
            epic_resp.raise_for_status()
            epic_key = epic_resp.json()["key"]

            # Create child stories
            created = []
            for story in stories:
                story_resp = await client.post(
                    f"{base}/rest/api/3/issue",
                    json={
                        "fields": {
                            "project": {"key": project_key},
                            "parent": {"key": epic_key},
                            "summary": story.get("summary", "Untitled"),
                            "description": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": story.get("description", ""),
                                            }
                                        ],
                                    }
                                ],
                            },
                            "issuetype": {"name": "Story"},
                        }
                    },
                )
                story_resp.raise_for_status()
                story_key = story_resp.json()["key"]
                created.append(f"{story_key}: {story.get('summary', '')}")

            result = f"Created epic {epic_key}: {epic_summary}\nStories:\n" + "\n".join(
                f"  - {c}" for c in created
            )
            log.info("jira_epic_created", epic_key=epic_key, story_count=len(created))
            return _ok(result)

    return [create_jira_epic]


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {text}"}], "isError": True}
