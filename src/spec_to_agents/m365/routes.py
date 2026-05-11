# Copyright (c) Microsoft. All rights reserved.

"""
FastAPI route registration for Microsoft 365 Agents SDK integration.

This module uses FastAPI's sub-application pattern to mount M365 endpoints
under /api with isolated JWT middleware. This keeps JWT auth scoped only
to M365 routes without affecting DevUI or other endpoints.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import Response
from microsoft_agents.hosting.fastapi import (
    CloudAdapter,
    JwtAuthorizationMiddleware,
    start_agent_process,  # type: ignore
)

if TYPE_CHECKING:
    from microsoft_agents.hosting.core import AgentApplication, AgentAuthConfiguration

from .state import WorkflowTurnState

logger = logging.getLogger(__name__)


def create_m365_subapp(
    agent_application: "AgentApplication[WorkflowTurnState]",
    adapter: CloudAdapter,
    auth_configuration: "AgentAuthConfiguration | None" = None,
) -> FastAPI:
    """
    Create a FastAPI sub-application for M365 endpoints.

    The sub-app has its own middleware stack, so JWT auth only applies
    to routes within this sub-app, not the parent app.

    Parameters
    ----------
    agent_application : AgentApplication[WorkflowTurnState]
        The M365 Agents SDK application with activity handlers
    adapter : CloudAdapter
        The CloudAdapter for processing activities
    auth_configuration : AgentAuthConfiguration | None, optional
        JWT authentication configuration. If None, runs in anonymous mode.

    Returns
    -------
    FastAPI
        The configured sub-application to mount at /api
    """
    m365_app = FastAPI(
        title="M365 Agents API",
        description="Microsoft 365 Agents SDK message endpoint",
    )

    # Add JWT middleware to sub-app only (not parent app)
    if auth_configuration:
        logger.info("🔐 Adding JWT authorization middleware to M365 sub-app")
        m365_app.state.agent_configuration = auth_configuration
        m365_app.add_middleware(JwtAuthorizationMiddleware)
    else:
        logger.warning("⚠️  Running without JWT authorization (local development only)")

    @m365_app.post("/messages", response_model=None)
    async def messages(request: Request) -> Response | dict[str, str]:  # type: ignore
        """
        Handle incoming activities from Microsoft 365 channels.

        This endpoint receives POST requests containing activity payloads
        from Teams, M365 Copilot, and other Microsoft 365 channels.
        """
        response = await start_agent_process(request, agent_application, adapter)
        return response or {"status": "ok"}

    @m365_app.get("/messages")
    async def messages_get() -> dict[str, str]:  # type: ignore
        """
        Health check endpoint for the messages route.

        This GET endpoint allows load balancers and health checks to verify
        the bot endpoint is accessible.
        """
        return {"status": "healthy", "endpoint": "/api/messages"}

    return m365_app


def initialize_m365_components(
    app: FastAPI,
    agent_application: "AgentApplication[WorkflowTurnState]",
    adapter: CloudAdapter,
    auth_configuration: "AgentAuthConfiguration | None" = None,
) -> None:
    """
    Initialize M365 integration by mounting a sub-application.

    This is the main entry point for integrating M365 Agents SDK with FastAPI.
    It creates a sub-app with JWT middleware and mounts it at /api, so the
    full endpoint path is /api/messages.

    Parameters
    ----------
    app : FastAPI
        The parent FastAPI application to mount onto
    agent_application : AgentApplication[WorkflowTurnState]
        The M365 Agents SDK application with activity handlers
    adapter : CloudAdapter
        The CloudAdapter for processing activities
    auth_configuration : AgentAuthConfiguration | None, optional
        JWT authentication configuration for validating incoming tokens.
    """
    m365_app = create_m365_subapp(agent_application, adapter, auth_configuration)
    app.mount("/api", m365_app)

    # Move our mount to the front of the route list.
    # This is necessary because DevUI has a catch-all UI mount at "" that would
    # otherwise intercept /api/* requests before our mount is checked.
    # Mounted apps are checked in registration order, so we need to be first.
    api_mount = [r for r in app.routes if getattr(r, "path", "") == "/api"]
    other_routes = [r for r in app.routes if getattr(r, "path", "") != "/api"]
    app.routes[:] = api_mount + other_routes

    logger.info("✅ M365 sub-app mounted at /api (endpoints: POST/GET /api/messages)")
