from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Required
    anthropic_api_key: str = ""
    redis_url: str = "redis://localhost:6379"

    # Jira (optional)
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""

    # GitHub (optional)
    github_token: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Agent defaults
    default_model: str = "claude-opus-4-6"
    max_agent_turns: int = 200
    max_budget_usd: float = 50.0
    workspace_base_dir: str = "/tmp/orchestrator-workspaces"

    # Gate timeouts
    gate_poll_interval_seconds: int = 5
    gate_max_wait_seconds: int = 86400  # 24 hours

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
