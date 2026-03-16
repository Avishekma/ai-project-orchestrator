"""Tests for the gate manager logic."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.gates.manager import GateManager
from orchestrator.models.schemas import GateDecision, GateStatus, GateType


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis_mock = AsyncMock()
    redis_mock.hset = AsyncMock()
    redis_mock.hgetall = AsyncMock(return_value={})
    redis_mock.hget = AsyncMock(return_value=None)
    redis_mock.publish = AsyncMock()
    redis_mock.blpop = AsyncMock(return_value=None)
    redis_mock.lpush = AsyncMock()
    return redis_mock


@pytest.fixture
def gate_mgr(mock_redis):
    mgr = GateManager()
    mgr._redis = mock_redis
    return mgr


@pytest.mark.asyncio
async def test_submit_decision(gate_mgr: GateManager, mock_redis):
    decision = GateDecision(
        decision=GateStatus.APPROVED,
        feedback="Looks good",
        decided_by="test_user",
    )
    await gate_mgr.submit_decision("gate-123", decision)
    mock_redis.lpush.assert_called_once()
    call_args = mock_redis.lpush.call_args
    assert call_args[0][0] == "gate:gate-123:decision"


@pytest.mark.asyncio
async def test_get_pending_gates_empty(gate_mgr: GateManager, mock_redis):
    mock_redis.hgetall.return_value = {}
    gates = await gate_mgr.get_pending_gates("proj-1")
    assert gates == []


@pytest.mark.asyncio
async def test_request_approval_timeout(gate_mgr: GateManager, mock_redis):
    """When no decision arrives, the gate times out as rejected."""
    # Override settings for fast timeout
    with patch("orchestrator.gates.manager.settings") as mock_settings:
        mock_settings.gate_poll_interval_seconds = 1
        mock_settings.gate_max_wait_seconds = 1

        mock_redis.blpop.return_value = None

        decision = await gate_mgr.request_approval(
            project_id="proj-1",
            gate_type=GateType.PLAN_REVIEW,
            summary="Test plan",
        )

        assert decision.decision == GateStatus.REJECTED
        assert "timed out" in decision.feedback.lower()
