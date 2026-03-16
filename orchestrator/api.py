"""FastAPI application — the HTTP interface to the orchestrator.

Endpoints:
    POST   /projects                          — Kick off a new project
    GET    /projects                          — List all projects
    GET    /projects/{id}/status              — Get current status
    GET    /projects/{id}/gates               — List pending approval gates
    GET    /projects/{id}/gates/{gate_id}     — Get a specific gate
    POST   /projects/{id}/gates/{gate_id}     — Submit approval/rejection
    GET    /projects/{id}/events              — SSE stream of real-time events
    GET    /health                            — Health check
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from orchestrator.agents.worker import run_project
from orchestrator.config.settings import settings
from orchestrator.gates.manager import gate_manager
from orchestrator.gates.state import state_manager
from orchestrator.middleware.logging import setup_logging
from orchestrator.models.schemas import GateDecision, GateStatus, ProjectCreate

log = structlog.get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop shared connections."""
    setup_logging(settings.log_level)
    await gate_manager.connect()
    await state_manager.connect()
    log.info("started", host=settings.host, port=settings.port)
    yield
    await gate_manager.disconnect()
    await state_manager.disconnect()
    log.info("stopped")


app = FastAPI(
    title="AI Project Orchestrator",
    description="Multi-agent workflow with human-in-the-loop gates",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ──


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Projects ──


@app.post("/projects", status_code=201)
async def create_project(req: ProjectCreate, bg: BackgroundTasks):
    """Create a new project run. Returns immediately; agent runs in background."""
    project_id = str(uuid.uuid4())

    await state_manager.create_project(project_id)

    bg.add_task(
        run_project,
        project_id=project_id,
        document=req.document,
        repo_url=req.repo_url,
        jira_project_key=req.jira_project_key,
        base_branch=req.base_branch,
        branch_prefix=req.branch_prefix,
    )

    log.info("project_created", project_id=project_id)
    return {"project_id": project_id, "status": "initializing"}


@app.get("/projects")
async def list_projects():
    """List all project IDs."""
    project_ids = await state_manager.list_projects()
    results = []
    for pid in project_ids:
        status = await state_manager.get_status(pid)
        if status:
            results.append(status.model_dump(mode="json"))
    return {"projects": results}


@app.get("/projects/{project_id}/status")
async def get_project_status(project_id: str):
    """Get the current status of a project run."""
    status = await state_manager.get_status(project_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return status.model_dump(mode="json")


# ── Gates ──


@app.get("/projects/{project_id}/gates")
async def get_pending_gates(project_id: str):
    """Get all pending human approval gates for a project."""
    gates = await gate_manager.get_pending_gates(project_id)
    return {"gates": [g.model_dump(mode="json") for g in gates]}


@app.get("/projects/{project_id}/gates/all")
async def get_all_gates(project_id: str):
    """Get all gates (pending + decided) for a project."""
    gates = await gate_manager.get_all_gates(project_id)
    return {"gates": [g.model_dump(mode="json") for g in gates]}


@app.get("/projects/{project_id}/gates/{gate_id}")
async def get_gate(project_id: str, gate_id: str):
    """Get a specific gate by ID."""
    gate = await gate_manager.get_gate(project_id, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found")
    return gate.model_dump(mode="json")


@app.post("/projects/{project_id}/gates/{gate_id}")
async def submit_gate_decision(project_id: str, gate_id: str, decision: GateDecision):
    """Submit a decision for a pending gate. Unblocks the waiting agent."""
    gate = await gate_manager.get_gate(project_id, gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail="Gate not found")
    if gate.status != GateStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Gate already {gate.status.value}")

    await gate_manager.submit_decision(gate_id, decision)
    return {"status": "submitted", "gate_id": gate_id, "decision": decision.decision}


# ── SSE Events ──


@app.get("/projects/{project_id}/events")
async def stream_events(project_id: str):
    """Server-Sent Events stream for real-time project updates."""
    status = await state_manager.get_status(project_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Project not found")

    async def event_generator():
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"project:{project_id}:events")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    try:
                        parsed = json.loads(data)
                        event_type = parsed.get("event_type", "message")
                        yield f"event: {event_type}\ndata: {data}\n\n"
                    except json.JSONDecodeError:
                        yield f"data: {data}\n\n"
        finally:
            await pubsub.unsubscribe()
            await redis.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
