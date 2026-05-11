# Copyright (c) Microsoft. All rights reserved.

"""
M365 AgentApplication builder for event planning workflow.

This module provides a factory function to create and configure an AgentApplication
with all activity handlers for the event planning workflow. It centralizes the
M365 SDK setup logic used by both main.py (DevUI integration) and m365_server.py
(standalone server).
"""

import logging
from os import environ
from typing import Any

from microsoft_agents.activity import load_configuration_from_env  # pyright: ignore[reportUnknownVariableType]
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    MemoryStorage,
)
from microsoft_agents.hosting.fastapi import CloudAdapter

from .handlers import execute_workflow
from .state import WorkflowTurnState

logger = logging.getLogger(__name__)


def create_agent_application() -> tuple[
    AgentApplication[WorkflowTurnState], CloudAdapter, AgentAuthConfiguration | None
]:
    """
    Create and configure an AgentApplication with activity handlers.

    This factory function handles all the boilerplate for creating an M365 Agents SDK
    AgentApplication, including:
    - Loading SDK configuration from environment variables
    - Setting up MSAL connection manager for outbound auth
    - Configuring JWT validation for incoming requests
    - Registering activity handlers for messages, help, errors, etc.

    Returns
    -------
    tuple[AgentApplication[WorkflowTurnState], CloudAdapter, AgentAuthConfiguration | None]
        A tuple containing the configured AgentApplication, CloudAdapter, and the auth
        configuration (which may be None if BOT_ID is not set).

    Raises
    ------
    ValueError
        If BOT_ID environment variable is not set.
    """
    bot_app_id = environ.get("BOT_ID")
    if not bot_app_id:
        raise ValueError("BOT_ID environment variable is required for M365 integration")

    logger.info("🤖 Creating M365 AgentApplication...")

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
    else:
        logger.warning("⚠️  No outbound auth configured - responses may fail in production channels")

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
    _register_activity_handlers(agent_app)

    return agent_app, adapter, auth_config


def _register_activity_handlers(agent_app: AgentApplication[WorkflowTurnState]) -> None:
    """
    Register all activity handlers on the AgentApplication.

    Parameters
    ----------
    agent_app : AgentApplication[WorkflowTurnState]
        The agent application to register handlers on.
    """

    def _ensure_state_attributes(state: WorkflowTurnState) -> None:
        """Ensure state has required custom attributes."""
        if not hasattr(state, "pending_requests"):
            state.pending_requests = {}  # type: ignore
        if not hasattr(state, "workflow_output"):
            state.workflow_output = None  # type: ignore
        if not hasattr(state, "is_workflow_complete"):
            state.is_workflow_complete = False  # type: ignore

    @agent_app.activity("installationUpdate")  # pyright: ignore[reportUnknownMemberType]
    async def _on_installation_update(  # pyright: ignore[reportUnusedFunction]  # noqa: RUF029
        context: Any, state: WorkflowTurnState
    ) -> None:
        """Handle installation updates."""
        _ensure_state_attributes(state)

    @agent_app.activity("conversationUpdate")  # pyright: ignore[reportUnknownMemberType]
    async def _on_conversation_update(  # pyright: ignore[reportUnusedFunction]
        context: Any, state: WorkflowTurnState
    ) -> None:
        """Handle conversation updates."""
        _ensure_state_attributes(state)

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
        _ensure_state_attributes(state)

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
