# Copyright (c) Microsoft. All rights reserved.

"""
Standalone Microsoft 365 Agents SDK server for event planning workflow.

This module provides a standalone HTTP server using the Microsoft 365 Agents SDK
with FastAPI hosting. It exposes the event planning workflow as a conversational
agent via the /api/messages endpoint.

NOTE: This standalone server is provided for backward compatibility. The preferred
approach is to use the integrated server via `uv run app` which combines the DevUI
and M365 endpoints on a single FastAPI server.

Usage
-----
    uv run m365-server

The server listens on port 3978 by default (configurable via PORT environment variable).
"""

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack
from logging import WARNING, getLogger
from os import environ
from typing import Any

import uvicorn
from agent_framework.observability import setup_observability
from dotenv import load_dotenv
from fastapi import FastAPI
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    MemoryStorage,
)
from microsoft_agents.hosting.fastapi import CloudAdapter

from spec_to_agents.container import AppContainer
from spec_to_agents.m365 import WorkflowTurnState, execute_workflow, initialize_m365_components

# Load environment variables at module import
load_dotenv()

# Enable observability (skip in container environments if not configured)
if not (os.getenv("CONTAINER_ENV") == "true" and not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")):
    setup_observability()

getLogger("azure.monitor.opentelemetry.exporter.export._base").setLevel(WARNING)
getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(WARNING)

# Configure logging with environment variable support
LOG_LEVEL = environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)

logger = logging.getLogger(__name__)
logger.info(f"👉 Logging initialized at level: {LOG_LEVEL}")

# Global variables for DI container and MCP tools
_app_container: AppContainer | None = None
_mcp_tools_initialized: bool = False


async def main() -> None:
    """
    Initialize and start the standalone M365 Agents server.

    This function sets up the DI container, creates the AgentApplication with
    activity handlers, and starts the FastAPI server on port 3978.
    """
    global _app_container, _mcp_tools_initialized

    logger.info("🚀 Initializing Standalone M365 Agents Server...")

    # Initialize DI container for MCP tools
    _app_container = AppContainer()
    _app_container.wire(modules=[__name__])

    # Initialize MCP tools with async context management
    async with _app_container.client():
        mcp_tools = _app_container.global_tools()

        if mcp_tools:
            async with AsyncExitStack() as stack:
                for name, tool in mcp_tools.items():
                    logger.info(f"🔧 Initializing MCP tool: {name}")
                    await stack.enter_async_context(tool)  # type: ignore[arg-type]

                _mcp_tools_initialized = True
                await _build_and_start_server()
        else:
            _mcp_tools_initialized = True
            await _build_and_start_server()


async def _build_and_start_server() -> None:
    """
    Build the AgentApplication and start the FastAPI server.

    This helper function creates the agent after MCP tools are initialized
    and configures the FastAPI server with M365 routes.
    """
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
        logger.info(f"🔐 Using {auth_type} for outbound auth (clientId: {client_id[:8] if client_id else 'N/A'}...)")
        connection_manager = MsalConnectionManager(**agents_sdk_config)  # pyright: ignore[reportUnknownArgumentType]
    else:
        logger.warning("⚠️  No outbound auth configured - responses may fail in production channels")

    # Create CloudAdapter
    adapter = CloudAdapter(connection_manager=connection_manager)  # pyright: ignore[reportArgumentType]

    # Configure JWT validation
    bot_app_id = environ.get("BOT_ID")
    bot_tenant_id = environ.get("TEAMS_APP_TENANT_ID")
    auth_config: AgentAuthConfiguration | None = None

    if bot_app_id:
        auth_config = AgentAuthConfiguration(
            client_id=bot_app_id,
            tenant_id=bot_tenant_id,
        )
        logger.info(f"🔐 JWT authentication configured (CLIENT_ID: {auth_config.CLIENT_ID})")
    else:
        logger.warning("⚠️  Running in anonymous mode (local development only)")

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

    # Create FastAPI app and register M365 routes
    app = FastAPI(title="Event Planning M365 Agent")
    initialize_m365_components(app, agent_app, auth_config)

    logger.info("✅ Agent application ready!")

    # Start FastAPI server
    port = int(environ.get("PORT", "3978"))
    logger.info(f"======== Running on http://localhost:{port} ========")
    logger.info("(Press CTRL+C to quit)")

    config = uvicorn.Config(app, host="localhost", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def cli() -> None:
    """
    Entry point for the m365-server command.

    This wrapper is required for pyproject.toml script entry points,
    which expect a synchronous callable.
    """
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n\n👋 Server stopped by user")
    except Exception as e:
        logger.error(f"\n\n❌ Server error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
