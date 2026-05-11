# Copyright (c) Microsoft. All rights reserved.

from os import environ

from agent_framework.observability import setup_observability
from dotenv import load_dotenv

# Load environment variables at module import
load_dotenv()

# Enable observability (skip in container environments if not configured)
if not (environ.get("CONTAINER_ENV") == "true" and not environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")):
    setup_observability()


def main() -> None:
    """Launch the branching workflow in DevUI with DI container and M365 integration."""
    import logging
    import sys

    import uvicorn
    from agent_framework.devui import DevServer
    from fastapi import FastAPI

    from spec_to_agents.agents import export_agents
    from spec_to_agents.container import AppContainer
    from spec_to_agents.m365 import create_agent_application, initialize_m365_components
    from spec_to_agents.workflow import export_workflow

    logging.getLogger("azure.monitor.opentelemetry.exporter.export._base").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

    # Configure logging with environment variable support
    LOG_LEVEL = environ.get("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(level=LOG_LEVEL, format="%(message)s", handlers=[logging.StreamHandler(sys.stdout)], force=True)
    logger = logging.getLogger(__name__)

    # Initialize DI container and wire modules for dependency injection
    container = AppContainer()
    container.wire(modules=[__name__])

    # Get port from environment (for container deployments) or use default
    port = int(environ.get("PORT", "8080"))
    # Bind to 0.0.0.0 in container environments for external access
    host = "0.0.0.0" if environ.get("CONTAINER_ENV") == "true" else "localhost"  # noqa: S104

    logger.info("Starting Agent Workflow DevUI with M365 Integration...")
    logger.info(f"Available at: http://{host}:{port}")

    # Load entities synchronously (no async context needed)
    workflows = export_workflow()
    agents = export_agents()

    # Create DevServer instance to get access to the FastAPI app
    server = DevServer(
        port=port,
        host=host,
    )
    server._pending_entities = workflows + agents  # type: ignore[assignment]

    # Get the underlying FastAPI app
    app: FastAPI = server.get_app()

    # --- M365 Agents SDK Integration ---
    # Only initialize M365 components if BOT_ID is configured
    bot_app_id = environ.get("BOT_ID")
    if bot_app_id:
        logger.info("🤖 Initializing M365 Agents SDK integration...")
        agent_app, adapter, auth_config = create_agent_application()
        initialize_m365_components(app, agent_app, adapter, auth_config)
        logger.info("✅ M365 routes registered on /api/messages")
    else:
        logger.info("ℹ️  BOT_ID not configured - M365 integration disabled")  # noqa: RUF001

    # Start the DevUI server
    logger.info(f"Starting Agent Framework DevUI on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
