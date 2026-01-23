# Copyright (c) Microsoft. All rights reserved.

import os
from os import environ

from agent_framework.observability import setup_observability
from dotenv import load_dotenv

# Load environment variables at module import
load_dotenv()

# Enable observability (skip in container environments if not configured)
if not (os.getenv("CONTAINER_ENV") == "true" and not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")):
    setup_observability()


def main() -> None:
    """Launch the branching workflow in DevUI with DI container and M365 integration."""
    import logging

    from agent_framework.devui import DevServer
    from fastapi import FastAPI

    from spec_to_agents.agents import export_agents
    from spec_to_agents.container import AppContainer
    from spec_to_agents.m365 import create_agent_application, initialize_m365_components
    from spec_to_agents.workflow import export_workflow

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    # Initialize DI container and wire modules for dependency injection
    container = AppContainer()
    container.wire(modules=[__name__])

    # Get port from environment (for container deployments) or use default
    port = int(os.getenv("PORT", "8080"))
    # Disable auto_open in container environments
    auto_open = os.getenv("ENVIRONMENT") != "production"
    # Bind to 0.0.0.0 in container environments for external access
    host = "0.0.0.0" if os.getenv("CONTAINER_ENV") == "true" else "localhost"  # noqa: S104

    logger.info("Starting Agent Workflow DevUI with M365 Integration...")
    logger.info(f"Available at: http://{host}:{port}")

    # Load entities synchronously (no async context needed)
    workflows = export_workflow()
    agents = export_agents()

    # Create DevServer instance to get access to the FastAPI app
    server = DevServer(
        entities=workflows + agents,
        port=port,
        host=host,
        auto_open=auto_open,
    )

    # Get the underlying FastAPI app
    app: FastAPI = server.get_app()

    # --- M365 Agents SDK Integration ---
    # Only initialize M365 components if BOT_ID is configured
    bot_app_id = environ.get("BOT_ID")
    if bot_app_id:
        logger.info("🤖 Initializing M365 Agents SDK integration...")
        agent_app, auth_config = create_agent_application()
        initialize_m365_components(app, agent_app, auth_config)
        logger.info("✅ M365 routes registered on /api/messages")
    else:
        logger.info("ℹ️  BOT_ID not configured - M365 integration disabled")  # noqa: RUF001

    # Start the DevUI server
    server.serve()


if __name__ == "__main__":
    main()
