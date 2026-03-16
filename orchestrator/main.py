"""Entrypoint — run the orchestrator API server."""

import uvicorn

from orchestrator.config.settings import settings


def main() -> None:
    uvicorn.run(
        "orchestrator.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
