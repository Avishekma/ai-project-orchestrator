#!/usr/bin/env bash
# Example: kick off a project and interact with gates.
# Usage: ./scripts/example_request.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

echo "=== Step 1: Create a project ==="
RESPONSE=$(curl -s -X POST "$BASE_URL/projects" \
    -H "Content-Type: application/json" \
    -d '{
        "document": "## Todo App\n\nBuild a REST API for a todo application with:\n1. User authentication (JWT)\n2. CRUD operations for todos\n3. Todo categories/tags\n4. Due dates with reminders\n5. Unit and integration tests\n\nTech stack: Python, FastAPI, PostgreSQL, SQLAlchemy.",
        "repo_url": "https://github.com/yourorg/todo-app.git",
        "jira_project_key": "TODO",
        "base_branch": "main"
    }')

PROJECT_ID=$(echo "$RESPONSE" | jq -r '.project_id')
echo "Project ID: $PROJECT_ID"
echo ""

echo "=== Step 2: Check status ==="
curl -s "$BASE_URL/projects/$PROJECT_ID/status" | jq .
echo ""

echo "=== Step 3: Stream events (Ctrl+C to stop) ==="
echo "Run in another terminal:"
echo "  curl -N $BASE_URL/projects/$PROJECT_ID/events"
echo ""

echo "=== Step 4: Check for pending gates ==="
echo "Run periodically:"
echo "  curl -s $BASE_URL/projects/$PROJECT_ID/gates | jq ."
echo ""

echo "=== Step 5: Approve a gate ==="
echo "When a gate appears, approve it with:"
echo '  curl -s -X POST '$BASE_URL'/projects/'$PROJECT_ID'/gates/<GATE_ID> \'
echo '    -H "Content-Type: application/json" \'
echo '    -d '"'"'{"decision": "approved", "feedback": "", "decided_by": "avi"}'"'"' | jq .'
echo ""

echo "=== Step 5b: Request changes ==="
echo '  curl -s -X POST '$BASE_URL'/projects/'$PROJECT_ID'/gates/<GATE_ID> \'
echo '    -H "Content-Type: application/json" \'
echo '    -d '"'"'{"decision": "changes_requested", "feedback": "Add rate limiting to all endpoints", "decided_by": "avi"}'"'"' | jq .'
