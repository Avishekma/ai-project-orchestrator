#!/usr/bin/env bash
# Quick local development setup.
# Usage: ./scripts/run_local.sh

set -euo pipefail

echo "=== AI Project Orchestrator — Local Dev ==="

# Check Redis
if ! command -v redis-cli &>/dev/null; then
    echo "Redis CLI not found. Install redis or use Docker:"
    echo "  docker run -d -p 6379:6379 redis:7-alpine"
    exit 1
fi

if ! redis-cli ping &>/dev/null; then
    echo "Redis is not running. Start it with:"
    echo "  redis-server --daemonize yes"
    exit 1
fi

echo "[OK] Redis is running"

# Check .env
if [ ! -f .env ]; then
    echo "No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "Please edit .env with your ANTHROPIC_API_KEY and restart."
    exit 1
fi

echo "[OK] .env exists"

# Install deps
echo "Installing dependencies..."
pip install -e ".[dev]" --quiet

# Run
echo ""
echo "Starting server on http://localhost:8000"
echo "API docs at http://localhost:8000/docs"
echo ""
python -m orchestrator.main
