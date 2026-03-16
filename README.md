# AI Project Orchestrator

A multi-agent AI workflow that takes a project specification, breaks it into stories, implements them with human review at every critical step, and deploys via CI/CD.

Built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with a FastAPI service layer for async execution and human-in-the-loop approval gates.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Client (Web UI / Slack / CLI / curl)                            │
└─────────────────────────┬────────────────────────────────────────┘
                          │ HTTP
┌─────────────────────────▼────────────────────────────────────────┐
│  FastAPI API Layer (orchestrator/api.py)                          │
│                                                                   │
│  POST /projects              → kick off a project                │
│  GET  /projects/:id/status   → poll current phase                │
│  GET  /projects/:id/gates    → list pending approvals            │
│  POST /projects/:id/gates/:g → approve / reject / request changes│
│  GET  /projects/:id/events   → SSE stream (real-time)            │
└──────────┬──────────────┬────────────────────────────────────────┘
           │              │
     BackgroundTask    Redis Pub/Sub
           │              │
┌──────────▼──────────────▼────────────────────────────────────────┐
│  Agent Worker (orchestrator/agents/worker.py)                     │
│                                                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐              │
│  │ Orchestrator │  │ Test Writer │  │ Code Reviewer│  (subagents) │
│  │    Agent     │→ │  Subagent   │  │   Subagent   │              │
│  └──────┬──────┘  └─────────────┘  └──────────────┘              │
│         │                                                         │
│  ┌──────▼──────────────────────────────────────────┐              │
│  │  Custom MCP Tools (orchestrator/tools/)          │              │
│  │  - Gate tools (block on Redis until human acts)  │              │
│  │  - Jira tools (create epics + stories)           │              │
│  │  - Status tools (emit phase updates)             │              │
│  └──────┬──────────────────────────────────────────┘              │
│         │                                                         │
│  ┌──────▼──────────────────────────────────────────┐              │
│  │  Built-in Tools (from Agent SDK)                 │              │
│  │  Read, Write, Edit, Bash, Glob, Grep, Agent      │              │
│  └─────────────────────────────────────────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

## How It Works — Step by Step

### Phase 1: Project Kickoff

```
Client                          API                         Worker
  │                              │                            │
  │  POST /projects              │                            │
  │  { document, repo_url, ... } │                            │
  │─────────────────────────────>│                            │
  │                              │  BackgroundTask: run_project()
  │  { project_id: "abc-123" }  │───────────────────────────>│
  │<─────────────────────────────│                            │
  │                              │                     Clone repo
  │                              │                     Start agent
```

What happens:
1. You POST a project spec (markdown text) along with a repo URL.
2. The API returns a `project_id` immediately — the agent runs in the background.
3. The worker clones the repo into an isolated workspace.
4. The orchestrator agent starts reading the project document.

### Phase 2: Planning + Story Creation

```
Worker (Agent)                    Redis                      Client
  │                                │                           │
  │  Reads project doc             │                           │
  │  Breaks into stories           │                           │
  │  Calls create_jira_epic        │                           │
  │  Calls request_plan_approval   │                           │
  │─────── PUBLISHES gate ────────>│                           │
  │                                │  GET /gates               │
  │        (BLOCKED)               │<──────────────────────────│
  │                                │  [gate with stories JSON] │
  │                                │──────────────────────────>│
  │                                │                           │
  │                                │  POST /gates/:id          │
  │                                │  { decision: "approved" } │
  │                                │<──────────────────────────│
  │<────── UNBLOCKED ─────────────│                           │
  │  Continues to implementation   │                           │
```

What happens:
1. The agent analyzes the spec and creates stories with titles, descriptions, and acceptance criteria.
2. It calls `create_jira_epic` to create tickets (or logs them in mock mode if Jira isn't configured).
3. It calls `request_plan_approval` — this is a **blocking gate**.
4. The gate tool publishes the plan to Redis and blocks via `BLPOP`.
5. You poll `GET /projects/:id/gates` to see the pending approval with the full plan.
6. You approve, reject, or request changes via `POST /projects/:id/gates/:gate_id`.
7. The decision unblocks the agent (via Redis `LPUSH`), and it either revises or proceeds.

### Phase 3: Implementation (per story)

```
Worker (Agent)
  │
  │  For each story:
  │  ├── git checkout -b feature/PROJ-101-user-auth
  │  ├── Implement code (Read → Edit → Write)
  │  ├── Delegate to test-writer subagent
  │  │   └── Writes tests, runs them, fixes failures
  │  ├── Delegate to code-reviewer subagent
  │  │   └── Reviews for security, quality, correctness
  │  ├── git commit + git push
  │  ├── gh pr create
  │  ├── Calls request_pr_approval  ← GATE (blocks)
  │  │   └── You review PR on GitHub + approve via API
  │  ├── gh pr merge
  │  └── update_project_status (stories_completed++)
  │
  │  Next story...
```

What happens:
1. For each approved story, the agent creates a feature branch.
2. It writes the implementation using the SDK's built-in file tools (Read, Write, Edit).
3. It spawns the **test-writer** subagent to write and run tests.
4. It spawns the **code-reviewer** subagent for a pre-review.
5. It commits, pushes, and opens a PR via `gh pr create`.
6. Another **blocking gate** — `request_pr_approval` — lets you review the PR.
7. Once approved, the agent merges and moves to the next story.

### Phase 4: Deploy

```
Worker (Agent)                    Redis                      Client
  │                                │                           │
  │  All stories merged            │                           │
  │  Runs full test suite          │                           │
  │  Calls request_deploy_approval │                           │
  │─────── PUBLISHES gate ────────>│                           │
  │        (BLOCKED)               │                           │
  │                                │  POST /gates/:id          │
  │                                │  { decision: "approved" } │
  │                                │<──────────────────────────│
  │<────── UNBLOCKED ─────────────│                           │
  │  gh workflow run deploy.yml    │                           │
  │  update_project_status(completed)                          │
```

## The Gate Mechanism (Human-in-the-Loop)

The key pattern that makes this work: **custom MCP tools that block on Redis pub/sub**.

```python
# Inside the agent tool (simplified):
async def request_plan_approval(args):
    gate_id = create_gate()
    redis.hset(f"project:{id}:gates", gate_id, gate_data)    # Store gate
    redis.publish(f"project:{id}:events", gate_event)          # Notify SSE clients

    # BLOCK here until human acts via the API
    decision = await redis.blpop(f"gate:{gate_id}:decision")  # Blocks!

    return decision  # Agent resumes with the decision

# When you call POST /projects/:id/gates/:gate_id:
async def submit_decision(gate_id, decision):
    redis.lpush(f"gate:{gate_id}:decision", decision)          # Unblocks the agent!
```

This decouples the agent from the approval UI. Approvals can come from:
- This REST API (curl, Postman, custom web UI)
- A Slack bot that calls the API
- A CI/CD pipeline
- Any HTTP client

## Project Structure

```
ai-project-orchestrator/
├── orchestrator/
│   ├── api.py                  # FastAPI app — all HTTP endpoints
│   ├── main.py                 # Entrypoint (uvicorn)
│   ├── agents/
│   │   ├── prompts.py          # System prompts (orchestrator + subagents)
│   │   └── worker.py           # Background task that runs the Agent SDK
│   ├── config/
│   │   └── settings.py         # Pydantic settings from env vars
│   ├── gates/
│   │   ├── manager.py          # Gate creation, blocking, decision handling
│   │   └── state.py            # Project phase tracking in Redis
│   ├── middleware/
│   │   └── logging.py          # Structured logging (structlog)
│   ├── models/
│   │   └── schemas.py          # Pydantic models (Project, Gate, Events)
│   └── tools/
│       ├── gate_tools.py       # MCP tools: plan/PR/deploy approval gates
│       ├── jira_tools.py       # MCP tools: Jira epic + story creation
│       ├── status_tools.py     # MCP tools: phase/progress reporting
│       └── registry.py         # Assembles tools into an MCP server
├── tests/
│   ├── test_api.py             # API endpoint tests
│   └── test_gates.py           # Gate manager logic tests
├── scripts/
│   ├── run_local.sh            # Local dev setup script
│   └── example_request.sh      # Example curl commands for the full flow
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .github/workflows/ci.yml    # GitHub Actions CI
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Quick Start

### Prerequisites

- Python 3.11+
- Redis (local or Docker)
- Claude Code CLI (`pip install claude-agent-sdk`)
- `ANTHROPIC_API_KEY` set in your environment
- `gh` CLI (for PR creation — `brew install gh` / `apt install gh`)

### Option 1: Local

```bash
# Clone and setup
git clone <this-repo>
cd ai-project-orchestrator
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY at minimum

# Start Redis
redis-server --daemonize yes

# Run
./scripts/run_local.sh

# API is at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### Option 2: Docker Compose

```bash
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY

cd docker
docker compose up --build

# API is at http://localhost:8000
```

## API Reference

### Create a Project

```bash
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{
    "document": "## My Project\n\nBuild a REST API with auth and CRUD...",
    "repo_url": "https://github.com/yourorg/myrepo.git",
    "jira_project_key": "PROJ",
    "base_branch": "main"
  }'
# → { "project_id": "abc-123", "status": "initializing" }
```

### Check Status

```bash
curl http://localhost:8000/projects/abc-123/status
# → { "phase": "awaiting_plan_review", "message": "...", "stories_total": 5, ... }
```

### List Pending Gates

```bash
curl http://localhost:8000/projects/abc-123/gates
# → { "gates": [{ "gate_id": "...", "gate_type": "plan_review", "summary": "...", "details": {...} }] }
```

### Approve / Reject a Gate

```bash
# Approve
curl -X POST http://localhost:8000/projects/abc-123/gates/gate-456 \
  -H "Content-Type: application/json" \
  -d '{"decision": "approved", "feedback": "", "decided_by": "avi"}'

# Request changes
curl -X POST http://localhost:8000/projects/abc-123/gates/gate-456 \
  -H "Content-Type: application/json" \
  -d '{"decision": "changes_requested", "feedback": "Add rate limiting", "decided_by": "avi"}'

# Reject
curl -X POST http://localhost:8000/projects/abc-123/gates/gate-456 \
  -H "Content-Type: application/json" \
  -d '{"decision": "rejected", "feedback": "Wrong approach", "decided_by": "avi"}'
```

### Stream Real-Time Events (SSE)

```bash
curl -N http://localhost:8000/projects/abc-123/events
# event: status_update
# data: {"event_type": "status_update", "project_id": "abc-123", "data": {"phase": "planning"}}
#
# event: gate_requested
# data: {"event_type": "gate_requested", ...}
#
# event: agent_message
# data: {"event_type": "agent_message", "data": {"text": "Creating feature branch..."}}
```

### List All Projects

```bash
curl http://localhost:8000/projects
```

## Design Decisions & Best Practices

### Why Redis for Gates (not a database)?

Gates are ephemeral — they exist only during a project run. Redis gives us:
- `BLPOP` for blocking until a decision arrives (the core gate mechanism)
- Pub/Sub for SSE event streaming
- Sub-millisecond latency
- No schema migrations

For durable audit logs, add Postgres as a secondary store.

### Why Background Tasks (not Celery/Temporal)?

For an MVP, FastAPI's `BackgroundTasks` is sufficient. The agent runs in a single long-lived coroutine. For production scale:
- **Temporal** — if you need durable execution that survives process restarts
- **Celery + Redis** — if you need horizontal scaling across workers
- **Kubernetes Jobs** — if you want container-per-project isolation

### Why MCP Tools (not direct function calls)?

The Claude Agent SDK uses [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) as its tool interface. Custom tools are registered as MCP servers, which:
- Provides a standard protocol for tool discovery and invocation
- Enables the same tools to be used across different agent frameworks
- Supports both in-process and external tool servers

### Security Considerations

- **Workspace isolation**: Each project gets its own directory under `WORKSPACE_BASE_DIR`. In production, use container-per-project with `--network none`.
- **No credential exposure**: The agent never sees API keys. Jira/GitHub credentials are injected via environment variables on the host side.
- **Permission scoping**: Each subagent has an explicit tool allowlist. The code-reviewer can only read, not write.
- **Sandboxing**: The Agent SDK's `bypassPermissions` mode is used because the API server is the trust boundary, not the agent's permission system. In multi-tenant deployments, use container sandboxing instead.
- **Gate timeouts**: Gates timeout after 24 hours (configurable) to prevent zombie agent processes.

### Error Handling

- The agent retries failed steps up to 3 times (enforced via the system prompt).
- If the agent hits `max_turns`, it's marked as failed.
- If the worker process crashes, the project is marked as failed with the exception.
- All tool calls are logged via structlog for debugging.
- Gate timeouts produce a rejection, stopping the workflow gracefully.

### Idempotency

- Jira ticket creation should use idempotency keys (add to production implementation).
- Git operations are naturally idempotent (push to same branch, create PR if not exists).
- Gate decisions are guarded — submitting to an already-decided gate returns 409.

## Extending

### Add a Slack Approval Bot

Create a bot that:
1. Subscribes to the SSE stream for `gate_requested` events.
2. Posts a Slack message with Approve/Reject buttons.
3. On button click, calls `POST /projects/:id/gates/:gate_id`.

### Add More Subagents

Add to `orchestrator/agents/worker.py`:

```python
agents={
    "security-scanner": AgentDefinition(
        description="Scans code for security vulnerabilities",
        prompt="Run security analysis using bandit, semgrep, etc.",
        tools=["Read", "Bash", "Glob", "Grep"],
    ),
    "doc-writer": AgentDefinition(
        description="Writes API documentation and READMEs",
        prompt="Generate comprehensive documentation for the codebase.",
        tools=["Read", "Write", "Glob", "Grep"],
    ),
}
```

### Add Persistent Storage

For audit trails, replace or supplement Redis with:
- **PostgreSQL** — for project history, gate decisions, agent logs
- **S3** — for storing generated artifacts (PRs, test reports)

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Your Anthropic API key |
| `REDIS_URL` | No | `redis://localhost:6379` | Redis connection URL |
| `JIRA_BASE_URL` | No | — | Jira instance URL. Empty = mock mode |
| `JIRA_EMAIL` | No | — | Jira authentication email |
| `JIRA_API_TOKEN` | No | — | Jira API token |
| `GITHUB_TOKEN` | No | — | GitHub token for `gh` CLI |
| `DEFAULT_MODEL` | No | `claude-opus-4-6` | Claude model ID |
| `MAX_AGENT_TURNS` | No | `200` | Max agent iterations before forced stop |
| `MAX_BUDGET_USD` | No | `50.0` | Max spend per project run |
| `WORKSPACE_BASE_DIR` | No | `/tmp/orchestrator-workspaces` | Where repos are cloned |
| `HOST` | No | `0.0.0.0` | API bind address |
| `PORT` | No | `8000` | API port |
| `LOG_LEVEL` | No | `info` | Log level (debug, info, warning, error) |

## License

MIT
