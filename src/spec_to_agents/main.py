# Copyright (c) Microsoft. All rights reserved.

import os
from os import environ
from typing import Any

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
    from microsoft_agents.activity import load_configuration_from_env
    from microsoft_agents.authentication.msal import MsalConnectionManager
    from microsoft_agents.hosting.core import (
        AgentApplication,
        AgentAuthConfiguration,
        MemoryStorage,
    )
    from microsoft_agents.hosting.fastapi import CloudAdapter

    from spec_to_agents.agents import export_agents
    from spec_to_agents.container import AppContainer
    from spec_to_agents.m365 import WorkflowTurnState, execute_workflow, initialize_m365_components
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

        # Load SDK configuration from environment
        env_dict: dict[str, str] = dict(environ)
        raw_config = load_configuration_from_env(env_dict)  # pyright: ignore[reportUnknownVariableType]
        agents_sdk_config: dict[str, Any] = {str(k): v for k, v in raw_config.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType]

        # Create storage and connection manager
        storage = MemoryStorage()
        connection_manager: MsalConnectionManager | None = None

        connections_config = agents_sdk_config.get("CONNECTIONS", {})
        if connections_config.get("SERVICE_CONNECTION"):
            auth_type = connections_config.get("SERVICE_CONNECTION", {}).get("SETTINGS", {}).get("AUTH_TYPE", "unknown")
            client_id = connections_config.get("SERVICE_CONNECTION", {}).get("SETTINGS", {}).get("CLIENTID", "")
            client_prefix = client_id[:8] if client_id else "N/A"
            logger.info(f"🔐 Using {auth_type} for outbound auth (clientId: {client_prefix}...)")
            connection_manager = MsalConnectionManager(**agents_sdk_config)  # pyright: ignore[reportUnknownArgumentType]

        # Create CloudAdapter
        adapter = CloudAdapter(connection_manager=connection_manager)  # pyright: ignore[reportArgumentType]

        # Configure JWT validation
        bot_tenant_id = environ.get("TEAMS_APP_TENANT_ID")
        auth_config = AgentAuthConfiguration(
            client_id=bot_app_id,
            tenant_id=bot_tenant_id,
        )
        logger.info(f"🔐 JWT auth configured (CLIENT_ID: {auth_config.CLIENT_ID})")

        # Create AgentApplication
        agent_app = AgentApplication[WorkflowTurnState](
            storage=storage,
            adapter=adapter,
            **agents_sdk_config,  # pyright: ignore[reportUnknownArgumentType]
        )

        # Register activity handlers
        @agent_app.activity("installationUpdate")  # pyright: ignore[reportUnknownMemberType]
        async def _on_installation_update(  # pyright: ignore[reportUnusedFunction]  # noqa: RUF029
            context: Any, state: WorkflowTurnState
        ) -> None:
            """Handle installation updates."""
            if not hasattr(state, "pending_requests"):
                state.pending_requests = {}  # type: ignore
            if not hasattr(state, "workflow_output"):
                state.workflow_output = None  # type: ignore
            if not hasattr(state, "is_workflow_complete"):
                state.is_workflow_complete = False  # type: ignore

        @agent_app.activity("conversationUpdate")  # pyright: ignore[reportUnknownMemberType]
        async def _on_conversation_update(  # pyright: ignore[reportUnusedFunction]
            context: Any, state: WorkflowTurnState
        ) -> None:
            """Handle conversation updates."""
            if not hasattr(state, "pending_requests"):
                state.pending_requests = {}  # type: ignore
            if not hasattr(state, "workflow_output"):
                state.workflow_output = None  # type: ignore
            if not hasattr(state, "is_workflow_complete"):
                state.is_workflow_complete = False  # type: ignore

            if context.activity.members_added:
                for member in context.activity.members_added:
                    if member.id != context.activity.recipient.id:
                        await context.send_activity(
                            "👋 **Welcome to the Event Planning Agent!**\n\n"
                            "I can help you plan events including:\n"
                            "- 🏢 Venue research and recommendations\n"
                            "- 💰 Budget analysis and cost management\n"
                            "- 🍽️ Catering coordination\n"
                            "- 📅 Logistics and scheduling\n\n"
                            "Tell me about your event to get started!"
                        )

        @agent_app.message("/help")  # pyright: ignore[reportUnknownMemberType]
        async def _on_help(  # pyright: ignore[reportUnusedFunction]
            context: Any, state: WorkflowTurnState
        ) -> None:
            """Handle help command."""
            await context.send_activity(
                "**Event Planning Agent Help**\n\n"
                "This agent coordinates multiple specialist agents to help plan your event:\n\n"
                "**Specialists:**\n"
                "- 🏢 **Venue Specialist**: Researches and recommends venues\n"
                "- 💰 **Budget Analyst**: Manages costs and financial constraints\n"
                "- 🍽️ **Catering Coordinator**: Handles food and beverage planning\n"
                "- 📅 **Logistics Manager**: Coordinates schedules and resources\n\n"
                "**Example requests:**\n"
                "- 'Plan a 50-person tech conference in Seattle with $25k budget'\n"
                "- 'I need a casual team offsite for 30 people in San Francisco'\n"
                "- 'Help me plan a formal gala for 200 guests in New York'\n\n"
                "Just describe your event and I'll coordinate the specialists!"
            )

        @agent_app.activity("message")  # pyright: ignore[reportUnknownMemberType]
        async def _on_message(  # pyright: ignore[reportUnusedFunction]
            context: Any, state: WorkflowTurnState
        ) -> None:
            """Handle incoming message activities."""
            if not hasattr(state, "pending_requests"):
                state.pending_requests = {}  # type: ignore
            if not hasattr(state, "workflow_output"):
                state.workflow_output = None  # type: ignore
            if not hasattr(state, "is_workflow_complete"):
                state.is_workflow_complete = False  # type: ignore

            user_message = context.activity.text or ""
            if not user_message.strip():
                await context.send_activity("Please provide a message about your event.")
                return
            await execute_workflow(context, state, user_message)

        @agent_app.error
        async def _on_error(  # pyright: ignore[reportUnusedFunction]
            context: Any, error: Exception
        ) -> None:
            """Handle uncaught errors."""
            logger.error(f"\n[on_error] Unhandled error: {error}", exc_info=True)
            await context.send_activity(
                "❌ **An unexpected error occurred.**\n\n"
                "The agent encountered an issue processing your request. "
                "Please try again or contact support if the problem persists."
            )

        # Register M365 routes on the FastAPI app
        initialize_m365_components(app, agent_app, auth_config)
        logger.info("✅ M365 routes registered on /api/messages")
    else:
        logger.info("ℹ️  BOT_ID not configured - M365 integration disabled")  # noqa: RUF001

    # Start the DevUI server
    server.serve()


if __name__ == "__main__":
    main()
