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

import uvicorn
from agent_framework.observability import setup_observability
from dotenv import load_dotenv
from fastapi import FastAPI

from spec_to_agents.container import AppContainer
from spec_to_agents.m365 import create_agent_application, initialize_m365_components

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
    # Create agent application using shared builder
    agent_app, adapter, auth_config = create_agent_application()

    # Create FastAPI app and register M365 routes
    app = FastAPI(title="Event Planning M365 Agent")
    initialize_m365_components(app, agent_app, adapter, auth_config)

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
