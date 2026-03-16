"""Tests for the API endpoints.

Uses fakeredis to avoid needing a real Redis instance.
Agent SDK calls are mocked since we're testing the API layer, not the agent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked Redis connections."""
    with (
        patch("orchestrator.gates.manager.gate_manager.connect", new_callable=AsyncMock),
        patch("orchestrator.gates.manager.gate_manager.disconnect", new_callable=AsyncMock),
        patch("orchestrator.gates.state.state_manager.connect", new_callable=AsyncMock),
        patch("orchestrator.gates.state.state_manager.disconnect", new_callable=AsyncMock),
    ):
        from orchestrator.api import app

        with TestClient(app) as c:
            yield c


def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_project(client: TestClient):
    with (
        patch("orchestrator.api.state_manager.create_project", new_callable=AsyncMock),
        patch("orchestrator.api.run_project", new_callable=AsyncMock),
    ):
        resp = client.post(
            "/projects",
            json={
                "document": "Build a todo app",
                "repo_url": "https://github.com/test/repo.git",
                "jira_project_key": "TEST",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "project_id" in data
        assert data["status"] == "initializing"


def test_get_project_not_found(client: TestClient):
    with patch(
        "orchestrator.api.state_manager.get_status",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.get("/projects/nonexistent/status")
        assert resp.status_code == 404


def test_gate_not_found(client: TestClient):
    with patch(
        "orchestrator.api.gate_manager.get_gate",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.get("/projects/proj-1/gates/gate-1")
        assert resp.status_code == 404


def test_submit_decision_gate_not_found(client: TestClient):
    with patch(
        "orchestrator.api.gate_manager.get_gate",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.post(
            "/projects/proj-1/gates/gate-1",
            json={"decision": "approved"},
        )
        assert resp.status_code == 404
