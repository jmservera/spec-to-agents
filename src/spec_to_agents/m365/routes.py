# Copyright (c) Microsoft. All rights reserved.

"""
FastAPI route registration for Microsoft 365 Agents SDK integration.

This module provides functions to register the /api/messages endpoint on a
FastAPI application, enabling the M365 Agents SDK bot to receive activities
from Microsoft 365 channels (Teams, Copilot, etc.).
"""

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI
from microsoft_agents.hosting.fastapi import JwtAuthorizationMiddleware, start_agent_process

if TYPE_CHECKING:
    from microsoft_agents.hosting.core import AgentApplication, AgentAuthConfiguration

from .state import WorkflowTurnState

logger = logging.getLogger(__name__)


def create_m365_routes(
    app: FastAPI,
    agent_application: "AgentApplication[WorkflowTurnState]",
    auth_configuration: "AgentAuthConfiguration | None" = None,
) -> None:
    """
    Register Microsoft 365 Agents SDK routes on a FastAPI application.

    This function adds the /api/messages endpoint that receives activities
    from Microsoft 365 channels and processes them through the agent application.

    Parameters
    ----------
    app : FastAPI
        The FastAPI application to register routes on
    agent_application : AgentApplication[WorkflowTurnState]
        The M365 Agents SDK application with activity handlers
    auth_configuration : AgentAuthConfiguration | None, optional
        JWT authentication configuration for validating incoming tokens.
        If None, the endpoint runs in anonymous mode (local development only).
    """
    # Add JWT authorization middleware if auth is configured
    if auth_configuration:
        logger.info("🔐 Adding JWT authorization middleware to FastAPI app")
        app.add_middleware(
            JwtAuthorizationMiddleware,
            auth_config=auth_configuration,
        )
    else:
        logger.warning("⚠️  Running without JWT authorization (local development only)")

    @app.post("/api/messages")
    async def messages() -> dict[str, str]:
        """
        Handle incoming activities from Microsoft 365 channels.

        This endpoint receives POST requests containing activity payloads
        from Teams, M365 Copilot, and other Microsoft 365 channels.

        Returns
        -------
        dict[str, str]
            A status response indicating the message was processed.
        """
        await start_agent_process(agent_application)
        return {"status": "ok"}

    @app.get("/api/messages")
    async def messages_get() -> dict[str, str]:
        """
        Health check endpoint for the messages route.

        This GET endpoint allows load balancers and health checks to verify
        the bot endpoint is accessible.

        Returns
        -------
        dict[str, str]
            A status response indicating the endpoint is healthy.
        """
        return {"status": "healthy", "endpoint": "/api/messages"}

    logger.info("✅ M365 routes registered: POST/GET /api/messages")


def initialize_m365_components(
    app: FastAPI,
    agent_application: "AgentApplication[WorkflowTurnState]",
    auth_configuration: "AgentAuthConfiguration | None" = None,
) -> None:
    """
    Initialize all M365 integration components on a FastAPI application.

    This is the main entry point for integrating M365 Agents SDK with FastAPI.
    It registers routes and sets up any required middleware.

    Parameters
    ----------
    app : FastAPI
        The FastAPI application to configure
    agent_application : AgentApplication[WorkflowTurnState]
        The M365 Agents SDK application with activity handlers
    auth_configuration : AgentAuthConfiguration | None, optional
        JWT authentication configuration for validating incoming tokens.
    """
    create_m365_routes(app, agent_application, auth_configuration)
