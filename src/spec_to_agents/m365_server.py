# Copyright (c) Microsoft. All rights reserved.

"""
Microsoft 365 Agents SDK server for event planning workflow.

This module provides an HTTP server endpoint using the Microsoft 365 Agents SDK
(microsoft-agents-hosting) to expose the event planning workflow as a conversational
agent. It handles incoming messages via the Activity protocol and integrates with
Teams, M365 Copilot, and other Microsoft platforms.

Pattern
-------
This follows the Microsoft 365 Agents SDK hosting pattern:
1. Create AgentApplication with storage and adapter
2. Register activity handlers using decorators (@AGENT_APP.activity)
3. Process messages by invoking the workflow
4. Return responses via TurnContext.send_activity()
5. Use TurnState to manage conversation state across turns

Architecture
------------
- **AgentApplication**: Replaces the CLI interaction with HTTP endpoints
- **TurnContext**: Provides access to incoming activity and conversation state
- **TurnState**: Tracks workflow state, pending requests, and outputs
- **CloudAdapter**: Handles communication with Microsoft channels
- **Workflow Integration**: Executes existing agent_framework workflow within activity handlers
"""

import asyncio
import sys
import traceback
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from os import environ

from aiohttp.web import Application, AppRunner, Request, Response, TCPSite
from dotenv import load_dotenv
from microsoft_agents.hosting.aiohttp import (
    CloudAdapter,
    jwt_authorization_middleware,
    start_agent_process,
)
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    MemoryStorage,
    TurnContext,
    TurnState as BaseTurnState,
)

from agent_framework import (
    AgentRunUpdateEvent,
    RequestInfoEvent,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
)
from spec_to_agents.container import AppContainer
from spec_to_agents.models.messages import HumanFeedbackRequest
from spec_to_agents.workflow.core import build_event_planning_workflow

# Load environment variables at module import
load_dotenv()


@dataclass
class WorkflowTurnState(BaseTurnState):
    """
    Extended turn state for tracking workflow execution across conversation turns.

    Attributes
    ----------
    pending_requests : dict[str, HumanFeedbackRequest]
        Map of request_id to HumanFeedbackRequest for human-in-the-loop interactions
    workflow_output : str | None
        Final output from completed workflow
    is_workflow_complete : bool
        Whether the workflow has finished execution
    """

    pending_requests: dict[str, HumanFeedbackRequest] = field(default_factory=dict)
    workflow_output: str | None = None
    is_workflow_complete: bool = False


# Global variables for DI container and MCP tools
# These are initialized in main() and accessed by activity handlers
_app_container: AppContainer | None = None
_mcp_tools_initialized: bool = False


async def start_server(
    agent_application: AgentApplication[WorkflowTurnState],
    auth_configuration: AgentAuthConfiguration | None,
) -> None:
    """
    Start the Microsoft 365 Agents SDK HTTP server.

    This function creates an aiohttp web application and configures it to handle
    incoming agent messages via the /api/messages endpoint. It follows the standard
    Microsoft 365 Agents SDK hosting pattern.

    Parameters
    ----------
    agent_application : AgentApplication[WorkflowTurnState]
        The configured agent application with activity handlers
    auth_configuration : AgentAuthConfiguration | None
        Optional authentication configuration for JWT validation
    """

    async def entry_point(req: Request) -> Response:
        """Handle incoming agent messages."""
        agent: AgentApplication[WorkflowTurnState] = req.app["agent_app"]
        adapter: CloudAdapter = req.app["adapter"]
        return await start_agent_process(req, agent, adapter)

    # Create aiohttp application with JWT middleware
    app = Application(
        middlewares=[jwt_authorization_middleware] if auth_configuration else []
    )

    # Register routes
    app.router.add_post("/api/messages", entry_point)
    
    async def health_check(_: Request) -> Response:
        """Health check endpoint."""
        return Response(status=200)
    
    app.router.add_get("/api/messages", health_check)

    # Store agent configuration in app state
    if auth_configuration:
        app["agent_configuration"] = auth_configuration
    app["agent_app"] = agent_application
    app["adapter"] = agent_application.adapter

    # Start HTTP server using AppRunner (works within existing event loop)
    port = int(environ.get("PORT", 3978))
    print(f"======== Running on http://localhost:{port} ========")
    print("(Press CTRL+C to quit)")

    runner = AppRunner(app)
    await runner.setup()
    site = TCPSite(runner, "localhost", port)
    await site.start()

    # Keep the server running
    try:
        await asyncio.Event().wait()  # Wait indefinitely
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()


async def _execute_workflow(
    context: TurnContext, state: WorkflowTurnState, user_message: str
) -> None:
    """
    Execute the event planning workflow and send responses.

    This function integrates the agent_framework workflow with the M365 SDK
    activity handler pattern. It processes streaming events from the workflow
    and converts them to activity messages.

    Parameters
    ----------
    context : TurnContext
        The turn context for sending activity messages
    state : WorkflowTurnState
        The conversation state tracking workflow progress
    user_message : str
        The user's message or response to process
    """
    try:
        # Build workflow with MCP tools automatically injected from DI container
        workflow = build_event_planning_workflow()

        # Check if responding to pending human-in-the-loop request
        pending_responses: dict[str, str] | None = None
        if state.pending_requests:
            # User is responding to a previous RequestInfoEvent
            pending_responses = {
                request_id: user_message
                for request_id in state.pending_requests.keys()
            }
            state.pending_requests.clear()

        # Execute workflow: first run or continue with responses
        if pending_responses:
            stream = workflow.send_responses_streaming(pending_responses)
        else:
            stream = workflow.run_stream(user_message)

        # Process streaming events
        async for event in stream:
            # Handle agent run updates (optional: could log or send typing indicators)
            if isinstance(event, AgentRunUpdateEvent):
                # In console.py this displays tool calls/results
                # In server mode, we skip detailed streaming for simplicity
                pass

            # Handle human-in-the-loop requests
            elif isinstance(event, RequestInfoEvent) and isinstance(
                event.data, HumanFeedbackRequest
            ):
                # Workflow is requesting human input
                feedback_request: HumanFeedbackRequest = event.data
                state.pending_requests[event.request_id] = feedback_request

                # Send prompt to user
                prompt_message = (
                    f"**{feedback_request.requesting_agent.title()} needs your input:**\n\n"
                    f"{feedback_request.prompt}"
                )
                await context.send_activity(prompt_message)

            # Handle final workflow output
            elif isinstance(event, WorkflowOutputEvent):
                state.workflow_output = str(event.data)
                state.is_workflow_complete = True

                # Send final event plan to user
                await context.send_activity(
                    f"**✨ Event Plan Complete:**\n\n{state.workflow_output}"
                )

            # Handle workflow status events (informational)
            elif isinstance(event, WorkflowStatusEvent):
                # Could log status transitions
                pass

    except Exception as e:
        await context.send_activity(
            f"❌ **Error executing workflow:** {str(e)}\n\n"
            "Please try again or contact support if the issue persists."
        )
        traceback.print_exc()
        raise


async def main() -> None:
    """
    Initialize the agent application and start the HTTP server.

    This function sets up the DI container, initializes MCP tools, creates the
    AgentApplication with activity handlers, and starts the HTTP server.
    """
    global _app_container, _mcp_tools_initialized

    print("🚀 Initializing Event Planning Agent Server...")

    # Initialize DI container for MCP tools
    _app_container = AppContainer()
    _app_container.wire(modules=[__name__])

    # Initialize MCP tools with async context management
    async with _app_container.client():
        mcp_tools = _app_container.global_tools()

        if mcp_tools:
            async with AsyncExitStack() as stack:
                for name, tool in mcp_tools.items():
                    print(f"🔧 Initializing MCP tool: {name}")
                    await stack.enter_async_context(tool)  # type: ignore[arg-type]

                _mcp_tools_initialized = True

                # Build and start agent
                await _build_and_start_agent()
        else:
            _mcp_tools_initialized = True
            await _build_and_start_agent()


async def _build_and_start_agent() -> None:
    """
    Build the AgentApplication with activity handlers and start the server.

    This helper function creates the agent after MCP tools are initialized.
    """
    # Create agent application with storage and adapter
    storage = MemoryStorage()
    adapter = CloudAdapter()

    agent_app = AgentApplication[WorkflowTurnState](
        storage=storage,
        adapter=adapter,
    )

    # Register activity handlers

    @agent_app.activity("conversationUpdate")
    async def on_conversation_update(
        context: TurnContext, state: WorkflowTurnState
    ) -> None:
        """Handle conversation updates (e.g., member added)."""
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

    @agent_app.message("/help")
    async def on_help(context: TurnContext, state: WorkflowTurnState) -> None:
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

    @agent_app.activity("message")
    async def on_message(context: TurnContext, state: WorkflowTurnState) -> None:
        """
        Handle incoming message activities.

        This is the main entry point for processing user messages. It executes
        the event planning workflow and manages conversation state.
        """
        user_message = context.activity.text or ""

        if not user_message.strip():
            await context.send_activity("Please provide a message about your event.")
            return

        # Execute workflow
        await _execute_workflow(context, state, user_message)

    @agent_app.error
    async def on_error(context: TurnContext, error: Exception) -> None:
        """Handle uncaught errors."""
        print(f"\n[on_error] Unhandled error: {error}", file=sys.stderr)
        traceback.print_exc()

        await context.send_activity(
            "❌ **An unexpected error occurred.**\n\n"
            "The agent encountered an issue processing your request. "
            "Please try again or contact support if the problem persists."
        )

    # Start HTTP server (async call)
    print("✅ Agent application ready!")
    await start_server(agent_app, None)


def cli() -> None:
    """
    Synchronous entry point for the server command.

    This wrapper is required for pyproject.toml script entry points,
    which expect a synchronous callable.
    """
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped by user")
    except Exception as e:
        print(f"\n\n❌ Server error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    cli()
