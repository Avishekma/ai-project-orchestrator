FROM python:3.11-slim

# System deps for git operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy app code
COPY orchestrator/ orchestrator/

# Create workspace directory
RUN mkdir -p /tmp/orchestrator-workspaces

# Non-root user for security
RUN useradd -m -s /bin/bash orchestrator
RUN chown -R orchestrator:orchestrator /app /tmp/orchestrator-workspaces
USER orchestrator

EXPOSE 8000

CMD ["python", "-m", "orchestrator.main"]
