"""Tool registry — assembles all MCP tools into a single server per project run.

Each project gets its own MCP server with tools scoped to that project's ID,
ensuring isolation between concurrent runs.
"""

from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server

from orchestrator.tools.gate_tools import create_gate_tools
from orchestrator.tools.jira_tools import create_jira_tools
from orchestrator.tools.status_tools import create_status_tools


def create_project_mcp_server(project_id: str):
    """Create a single MCP server bundling all custom tools for a project run."""
    tools = [
        *create_gate_tools(project_id),
        *create_jira_tools(project_id),
        *create_status_tools(project_id),
    ]
    return create_sdk_mcp_server(f"orchestrator-{project_id}", tools=tools)
