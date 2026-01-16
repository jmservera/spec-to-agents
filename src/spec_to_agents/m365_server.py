# Copyright (c) Microsoft. All rights reserved.

"""
Microsoft 365 Agents SDK server for event planning workflow.

This module provides an HTTP server endpoint using the Microsoft 365 Agents SDK
(microsoft-agents-hosting) to expose the event planning workflow as a conversational
agent. It handles incoming messages via the Activity protocol and integrates with
Teams, M365 Copilot, and other Microsoft platforms.

Implementation example: https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/quickstart?pivots=python
More info at: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/testing?tabs=python
Activity protocol: https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/activity-protocol

Pattern
-------
This follows the Microsoft 365 Agents SDK hosting pattern:
1. Create AgentApplication with CloudAdapter for channel communication
2. Configure Bot Service authentication via MsalConnectionManager and JWT middleware
3. Register activity handlers using decorators (@agent_app.activity, @agent_app.message)
4. Process messages by invoking the workflow with streaming event handling
5. Return responses via TurnContext.send_activity() or streaming APIs

Architecture
------------
- **AgentApplication**: Provides HTTP endpoint for M365 channels (Teams, Copilot, etc.)
- **MsalConnectionManager**: Handles Bot Service authentication using Managed Identity or app credentials
- **JWT Middleware**: Validates incoming requests from Microsoft channels
- **TurnContext**: Provides access to incoming activity and streaming response APIs
- **WorkflowTurnState**: Tracks pending human-in-the-loop requests across conversation turns
- **Workflow Cache**: Preserves workflow instances per conversation to maintain agent service threads
- **CloudAdapter**: Handles communication with Microsoft channels
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from logging import WARNING, getLogger
from os import environ
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_framework.observability import setup_observability
from aiohttp.web import Application, AppRunner, Request, Response, TCPSite
from dotenv import load_dotenv
from microsoft_agents.activity import (
    Activity,
    ActivityTypes,
    Attachment,
    load_configuration_from_env,  # pyright: ignore[reportUnknownVariableType]
)
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import (
    CloudAdapter,
    jwt_authorization_middleware,  # pyright: ignore[reportUnknownVariableType]
    start_agent_process,  # pyright: ignore[reportUnknownVariableType]
)
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    MemoryStorage,
    TurnContext,
)
from microsoft_agents.hosting.core import (
    TurnState as BaseTurnState,
)

if TYPE_CHECKING:
    from agent_framework import Workflow

from agent_framework import (
    AgentRunUpdateEvent,
    ExecutorCompletedEvent,
    RequestInfoEvent,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
)

from spec_to_agents.container import AppContainer
from spec_to_agents.models.messages import HumanFeedbackRequest
from spec_to_agents.workflow.core import build_event_planning_workflow

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
    handlers=[logging.StreamHandler(sys.stdout)],  # Explicitly write to stdout
    force=True,  # Override any existing configuration
)

# Initialize module logger
logger = logging.getLogger(__name__)

logger.info(f"👉 Logging initialized at level: {LOG_LEVEL} {logger.getEffectiveLevel()}")


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

    pending_requests: dict[str, HumanFeedbackRequest] = field(
        default_factory=lambda: {}  # pyright: ignore[reportUnknownLambdaType]
    )
    workflow_output: str | None = None
    is_workflow_complete: bool = False


# Global variables for DI container and MCP tools
# These are initialized in main() and accessed by activity handlers
_app_container: AppContainer | None = None
_mcp_tools_initialized: bool = False

# Workflow instance cache to preserve agent service threads across HTTP requests
# Key: conversation_id, Value: workflow instance with agents
_workflow_cache: dict[str, "Workflow"] = {}


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
        result = await start_agent_process(req, agent, adapter)  # pyright: ignore[reportUnknownVariableType]
        return result or Response(status=200)

    # Create aiohttp application with JWT middleware
    app = Application(middlewares=[jwt_authorization_middleware])  # pyright: ignore[reportUnknownArgumentType]

    # Register routes
    app.router.add_post("/api/messages", entry_point)

    async def health_check(_: Request) -> Response:  # noqa: RUF029
        """Health check endpoint."""
        return Response(status=200)

    app.router.add_get("/api/messages", health_check)

    # Store agent configuration in app state (required by jwt_authorization_middleware)
    app["agent_configuration"] = auth_configuration
    app["agent_app"] = agent_application
    app["adapter"] = agent_application.adapter

    # Start HTTP server using AppRunner (works within existing event loop)
    port = int(environ.get("PORT", 3978))
    logger.info(f"======== Running on http://localhost:{port} ========")
    logger.info("(Press CTRL+C to quit)")

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


async def send_typing(context: TurnContext, message: str | None = None) -> None:
    """Send a typing indicator to the user."""
    try:
        logger.debug("💬 Sending typing indicator...")
        # Activity constructor requires 'from_property' but it's auto-set by the adapter
        if message:
            typingActivity = Activity(  # pyright: ignore[reportCallIssue]
                type=ActivityTypes.typing,
                text=message,
            )
        else:
            typingActivity = Activity(  # pyright: ignore[reportCallIssue]
                type=ActivityTypes.typing,
            )
        await context.send_activity(typingActivity)
    except Exception as e:
        logger.warning(f"⚠️  Could not send typing indicator: {e}")


def generate_context_card(
    agent_name: str,
    summary: str,
    next_agent: str | None,
    user_input_needed: bool,
) -> Attachment | None:
    """
    Send an adaptive card showing the agent's current thinking context.

    Parameters
    ----------
    agent_name : str
        The name of the agent requesting input
    summary : str
        Summary of the agent's current analysis/recommendations
    next_agent : str | None
        The next agent to consult (if any)
    user_input_needed : bool
        Whether user input is needed
    """
    try:
        # Load adaptive card template
        card_path = Path(__file__).parent / "adaptive_cards" / "agent_context_card.json"
        with open(card_path, "r", encoding="utf-8") as f:
            card_template = json.load(f)

        # Prepare data for card
        # Format agent name nicely
        formatted_agent_name = agent_name.replace("_", " ").title()

        # Format next agent
        formatted_next_agent = next_agent.replace("_", " ").title() if next_agent else "None"

        # Status based on user_input_needed
        status = "⏸️ Waiting for input" if user_input_needed else "✅ Processing"

        # Show next agent section only if next_agent is not None
        show_next_agent = next_agent is not None

        # Sanitize inputs by properly escaping them for JSON
        # Use json.dumps to escape special characters (newlines, tabs, quotes, etc.)
        def sanitize_for_json(text: str) -> str:
            """Escape text for safe insertion into JSON string."""
            # json.dumps adds quotes, so we strip them and unescape double escapes
            return json.dumps(text)[1:-1]

        # Replace template variables with sanitized values
        card_json = json.dumps(card_template)
        card_json = card_json.replace("${agent_name}", sanitize_for_json(formatted_agent_name))  # noqa: RUF027
        card_json = card_json.replace("${summary}", sanitize_for_json(summary))  # noqa: RUF027
        card_json = card_json.replace("${next_agent}", sanitize_for_json(formatted_next_agent))  # noqa: RUF027
        card_json = card_json.replace("${status}", sanitize_for_json(status))  # noqa: RUF027
        card_json = card_json.replace("${show_next_agent}", str(show_next_agent).lower())  # noqa: RUF027
        card_data = json.loads(card_json)

        # Create attachment
        return Attachment(
            content_type="application/vnd.microsoft.card.adaptive",
            content=card_data,
        )
    except Exception as e:
        logger.warning(f"⚠️  Could not send agent context card: {e}")
        logger.debug(traceback.format_exc())
    return None


# see how to stream: https://microsoft.github.io/teams-sdk/python/essentials/sending-messages/
async def _execute_workflow(context: TurnContext, state: WorkflowTurnState, user_message: str) -> None:
    """
    Execute the event planning workflow and send responses.

    This function integrates the agent_framework workflow with the M365 SDK
    activity handler pattern. It processes streaming events from the workflow
    and converts them to activity messages.

    NOTE: Workflow instances are cached per conversation_id in `_workflow_cache`
    to preserve agent service threads across HTTP requests. This ensures
    conversation history is maintained without requiring manual message tracking.

    Parameters
    ----------
    context : TurnContext
        The turn context for sending activity messages
    state : WorkflowTurnState
        The conversation state tracking workflow progress
    user_message : str
        The user's message or response to process
    """
    logger.info(
        "🚀 Executing event planning workflow...\n\tStart time: %s\n\tConversation ID: %s",
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        context.activity.conversation.id,
    )

    start_time = time.time()
    last_typing_time = time.time()
    last_progress_message_time = time.time()
    last_notified_agent = None

    # Access streaming response - it may be None in non-streaming contexts
    streaming = context.streaming_response
    stream_timed_out = False  # Track if streaming timed out
    streaming_stopped = False  # Track if we stopped streaming manually

    def is_stream_alive() -> bool:
        """Check if the streaming response is still usable."""
        if not streaming or streaming_stopped or stream_timed_out:
            return False
        try:
            return not streaming._ended  # pyright: ignore[reportPrivateUsage]
        except Exception:
            return False

    def queue_info_update(info: str):
        nonlocal stream_timed_out
        nonlocal last_progress_message_time

        try:
            if is_stream_alive():
                streaming.queue_informative_update(info)  # pyright: ignore[reportOptionalMemberAccess]
                logger.debug("📨 Sent progress update message")
            last_progress_message_time = time.time()
        except Exception as e:
            if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                logger.warning("⚠️ Stream timed out during progress update")
                stream_timed_out = True
            else:
                logger.warning(f"⚠️  Could not send progress message: {e}")

    if streaming:
        logger.debug(f"🐞 Streaming enabled: {streaming._is_streaming_channel}")  # pyright: ignore[reportPrivateUsage]
        queue_info_update("Starting workflow execution...")

    # Track content for recovery in case of stream failure
    streamed_content: list[str] = []
    streamed_attachments: list[Attachment] = []

    try:
        # Get or create workflow instance for this conversation
        # CRITICAL: We must reuse the same workflow + agent instances to preserve
        # service thread IDs across HTTP requests, otherwise conversation history is lost
        conversation_id = context.activity.conversation.id

        if conversation_id in _workflow_cache:
            workflow = _workflow_cache[conversation_id]
            logger.info(f"♻️  Reusing cached workflow for conversation {conversation_id[:8]}...")
        else:
            workflow = build_event_planning_workflow()
            _workflow_cache[conversation_id] = workflow
            logger.info(f"🆕 Created new workflow for conversation {conversation_id[:8]}...")

        # Check if responding to pending human-in-the-loop request
        if state.pending_requests:
            # User is responding to a previous RequestInfoEvent
            pending_responses = {request_id: user_message for request_id in state.pending_requests}
            state.pending_requests.clear()

            logger.info("🔄 Resuming from HITL")
            stream = workflow.send_responses_streaming(pending_responses)
        else:
            # Normal conversation flow: agent service threads handle history
            stream = workflow.run_stream(user_message)

        # Agent display names for user-friendly messages
        agent_names = {
            "venue": "🏢 Venue Specialist",
            "budget": "💰 Budget Analyst",
            "catering": "🍽️ Catering Coordinator",
            "logistics": "📅 Logistics Manager",
            "coordinator": "🎯 Event Coordinator",
        }

        queue_info_update("Starting workflow streaming...")  # pyright: ignore[reportOptionalMemberAccess]

        try:
            async for event in stream:  # pyright: ignore[reportUnknownVariableType]
                # Check if stream is still alive before sending updates
                if not is_stream_alive() and not streaming_stopped:
                    logger.warning("⚠️ Stream ended/timed out, continuing workflow without streaming...")
                    # Don't break - continue processing events, just skip streaming updates

                # Send typing indicator at most once every 5 seconds
                current_time = time.time()
                if current_time - last_typing_time >= 5.0:
                    last_typing_time = current_time
                    try:
                        await send_typing(context)
                    except Exception as e:
                        # Check if this is a stream timeout error
                        if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                            logger.warning("⚠️ Stream timed out, switching to non-streaming mode")
                            stream_timed_out = True
                        else:
                            logger.warning(f"⚠️ Failed to send typing indicator: {e}")
                        # Continue processing - don't break

                if current_time - start_time >= 90.0 and not streaming_stopped:
                    streaming_stopped = True
                    logger.warning("⚠️ Workflow execution time exceeded 90 seconds, stopping updates")
                    try:
                        streaming.queue_text_chunk(  # pyright: ignore[reportOptionalMemberAccess]
                            "⏳ Still working on your event plan... I will send the final details shortly."
                        )
                        await streaming.end_stream()  # pyright: ignore[reportOptionalMemberAccess]
                        logger.debug("✅ Stream ended due to time limit")
                    except Exception as e:
                        if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                            stream_timed_out = True
                        else:
                            logger.warning(f"⚠️ Failed to end stream after time limit: {e}")

                # Send progress messages every 10 seconds to prevent early timeout
                # M365 Copilot expects responses within ~20-30 seconds
                elif current_time - last_progress_message_time >= 10.0:
                    queue_info_update(
                        "⏳ Still working on your event plan... "
                        "This may take a moment as I coordinate with specialist agents."
                    )

                # Handle agent run updates - notify user which agent is working
                if isinstance(event, AgentRunUpdateEvent):
                    # Extract agent name from event data
                    agent_data = event.data  # pyright: ignore[reportUnknownMemberType]
                    agent_name: str | None = None

                    # Try to get agent name from event data
                    if agent_data is not None and hasattr(agent_data, "author_name"):
                        author = getattr(agent_data, "author_name", None)
                        if author:
                            agent_name = str(author).lower()
                    elif isinstance(agent_data, dict) and "name" in agent_data:
                        agent_name = str(agent_data["name"]).lower()  # pyright: ignore[reportUnknownArgumentType]

                    # Notify user when a new agent starts working
                    if agent_name and agent_name != last_notified_agent:
                        # Map agent name to friendly display name
                        display_name = agent_names.get(agent_name, f"🤖 {agent_name.replace('_', ' ').title()}")
                        try:
                            message = f"Consulting with {display_name}..."
                            queue_info_update(message)
                            logger.debug(f"👤 Notified user about agent: {display_name}")
                            last_notified_agent = agent_name
                        except Exception as e:
                            if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                                stream_timed_out = True
                            else:
                                logger.warning(f"⚠️  Could not send agent notification: {e}")

                # Handle human-in-the-loop requests
                elif isinstance(event, RequestInfoEvent) and isinstance(event.data, HumanFeedbackRequest):
                    # Workflow is requesting human input
                    feedback_request: HumanFeedbackRequest = event.data
                    state.pending_requests[event.request_id] = feedback_request

                    # Try to extract context from last conversation message
                    summary = None
                    next_agent = None
                    user_input_needed = True

                    if feedback_request.conversation and len(feedback_request.conversation) > 0:
                        last_message = feedback_request.conversation[-1]

                        # Check if last message has content with text containing JSON
                        try:
                            # Get the text from the content
                            content_text = None
                            if hasattr(last_message, "text"):
                                content_text = last_message.text

                            if content_text:
                                # Try to parse as JSON
                                context_data = json.loads(content_text)
                                summary = context_data.get("summary")
                                next_agent = context_data.get("next_agent")
                                user_input_needed = context_data.get("user_input_needed", True)
                                logger.debug(
                                    f"📝 Extracted context from last message: "
                                    f"summary={bool(summary)}, next_agent={next_agent}"
                                )
                        except (json.JSONDecodeError, AttributeError) as e:
                            logger.warning(f"⚠️  Could not parse last message content as JSON: {e}")

                    # Send adaptive card with agent context if we have summary
                    if summary:
                        card = generate_context_card(
                            agent_name=feedback_request.requesting_agent,
                            summary=summary,
                            next_agent=next_agent,
                            user_input_needed=user_input_needed,
                        )
                        if card:
                            streamed_attachments.append(card)  # Track for recovery if stream fails
                            try:
                                if is_stream_alive():
                                    streaming.set_attachments([card])  # pyright: ignore[reportOptionalMemberAccess]
                            except Exception as e:
                                if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                                    stream_timed_out = True
                                else:
                                    logger.warning(f"⚠️  Could not set citations/attachments: {e}")

                    # Send prompt to user
                    prompt_message = (
                        f"**{feedback_request.requesting_agent.replace('_', ' ').title()} needs your input:**\n\n"
                        f"{feedback_request.prompt}"
                    )

                    # Try streaming first, fall back to regular activity if stream is dead
                    try:
                        streamed_content.append(prompt_message)
                        if is_stream_alive():
                            streaming.queue_text_chunk(prompt_message)  # pyright: ignore[reportOptionalMemberAccess]
                            logger.info("✋ Sent human feedback request to user (streaming)")
                        else:
                            # Stream is dead, send as regular activity with attachments if any
                            if streamed_attachments:
                                # Create Activity object to include attachments
                                activity = Activity(  # pyright: ignore[reportCallIssue]
                                    type="message",
                                    text="\n".join(streamed_content),
                                    attachments=streamed_attachments,
                                )
                                logger.info(
                                    f"✅ Recovered {len(streamed_attachments)} attachments from tracking for HITL"
                                )
                            else:
                                activity = Activity(  # pyright: ignore[reportCallIssue]
                                    type="message",
                                    text="\n".join(streamed_content),
                                )

                            logger.info("✋ Sending human feedback request via regular activity (stream timed out)")
                            await context.send_activity(activity)
                    except Exception as e:
                        # If streaming failed, try regular activity as fallback
                        if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                            stream_timed_out = True
                            logger.warning("⚠️ Stream timed out, falling back to regular activity")
                            try:
                                await context.send_activity(prompt_message)
                                logger.info("✋ Sent human feedback request via fallback activity")
                            except Exception as fallback_error:
                                logger.error(f"❌ Failed to send human feedback request via fallback: {fallback_error}")
                                break
                        else:
                            logger.error(f"❌ Failed to send human feedback request: {e}")
                            break

                # Handle final workflow output
                elif isinstance(event, WorkflowOutputEvent):
                    state.workflow_output = str(event.data)
                    state.is_workflow_complete = True

                    # CRITICAL: End stream BEFORE sending final output
                    try:
                        if is_stream_alive():
                            await streaming.end_stream()  # pyright: ignore[reportOptionalMemberAccess]
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to end stream for workflow output: {e}")

                    # Send final event plan to user

                    # Try to extract summary from JSON, otherwise use raw output
                    output_text: str = state.workflow_output
                    try:
                        output_data = json.loads(state.workflow_output)
                        if isinstance(output_data, dict) and "summary" in output_data:
                            if isinstance(output_data["summary"], str):
                                output_text = str(output_data["summary"])
                            else:
                                output_text = json.dumps(output_data["summary"], indent=2)
                    except (json.JSONDecodeError, TypeError):
                        # Not JSON or no summary field, use raw output
                        output_text = state.workflow_output
                    try:
                        await context.send_activity(f"**✨ Event Plan Complete:**\n\n{output_text}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send final output: {e}")
                # Handle workflow status events (informational)
                elif isinstance(event, WorkflowStatusEvent):
                    pass  # Status events don't contain checkpoint info

                # Handle executor completed events (informational)
                elif isinstance(event, ExecutorCompletedEvent):
                    pass  # Can be used in future for agent completion tracking

            # Loop completed - end stream if still alive
            try:
                if is_stream_alive():
                    await streaming.end_stream()  # pyright: ignore[reportOptionalMemberAccess]
                    logger.debug("✅ Stream ended after event loop completed")
            except Exception as e:
                if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                    stream_timed_out = True
                else:
                    logger.warning(f"⚠️ Failed to end stream after loop: {e}")

        except Exception as stream_error:
            # Agent framework streaming error (e.g., Azure OpenAI API error)
            logger.error(f"⚠️ Error during stream iteration: {type(stream_error).__name__}: {stream_error!s}")
            logger.error(f"Stream error details:\n{traceback.format_exc()}")
            raise

    except Exception as e:
        # Log detailed error information for debugging
        error_type = type(e).__name__
        logger.error(f"❌ Workflow execution failed: [{error_type}] {e!s}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")

        # Check if stream ended early (timeout)
        stream_ended_early = False

        try:
            if is_stream_alive():
                await streaming.end_stream()  # pyright: ignore[reportOptionalMemberAccess]
            elif streaming:
                stream_ended_early = True
                logger.warning("⚠️ Stream already ended (likely due to timeout)")
        except RuntimeError:
            # Stream already ended (likely due to timeout)
            stream_ended_early = True
            logger.warning("⚠️ Stream already ended, skipping end_stream() call")
        except Exception as end_error:
            if "exceeded streaming time" in str(end_error).lower() or "forbidden" in str(end_error).lower():
                stream_ended_early = True
                logger.warning("⚠️ Stream timed out during error handling")
            else:
                logger.warning(f"⚠️ Failed to end stream: {end_error}")

        # Build error message
        error_message = f"❌ **Workflow execution interrupted:** {e!s}\n\n"

        # If stream failed and we have tracked content, include it
        if stream_ended_early and streamed_content:
            error_message += "**Partial results before interruption:**\n\n"
            error_message += "\n\n".join(str(c) for c in streamed_content)
            error_message += "\n\n---\n\n"
            logger.info(f"✅ Recovered {len(streamed_content)} content chunks from tracking")

        error_message += "Please try again or contact support if the issue persists."

        # Send as regular activity with any tracked attachments
        if stream_ended_early and streamed_attachments:
            # Create Activity object to include attachments
            activity = Activity(  # pyright: ignore[reportCallIssue]
                type="message",
                text=error_message,
                attachments=streamed_attachments,
            )
            logger.info(f"✅ Recovered {len(streamed_attachments)} attachments from tracking")
            await context.send_activity(activity)
        else:
            # Send simple text message
            await context.send_activity(error_message)

        logger.error(f"Error executing workflow: {e}", exc_info=True)
        raise


async def main() -> None:
    """
    Initialize the agent application and start the HTTP server.

    This function sets up the DI container, initializes MCP tools, creates the
    AgentApplication with activity handlers, and starts the HTTP server.
    """
    global _app_container, _mcp_tools_initialized

    logger.info("🚀 Initializing Event Planning Agent Server...")

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

                # Build and start agent
                await _build_and_start_agent()
        else:
            _mcp_tools_initialized = True
            await _build_and_start_agent()


async def _build_and_start_agent() -> None:
    """
    Build the AgentApplication with activity handlers and start the server.

    This helper function creates the agent after MCP tools are initialized.

    Load configuration from environment variables
    This reads MICROSOFT_APP_ID, MICROSOFT_APP_TYPE, MICROSOFT_APP_TENANT_ID, etc.
    CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=client-id
    CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=tenant-id
    CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE=UserManagedIdentity
    see https://github.com/microsoft/Agents/blob/main/samples/python/quickstart/README.md
    NOTE: load_configuration_from_env returns dict[Unknown, Unknown] due to incomplete type stubs
    """
    env_dict: dict[str, str] = dict(environ)
    raw_config = load_configuration_from_env(env_dict)  # pyright: ignore[reportUnknownVariableType]
    agents_sdk_config: dict[str, Any] = {str(k): v for k, v in raw_config.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType]

    # Create storage
    storage = MemoryStorage()

    # Create connection manager using MSAL authentication
    # This handles token acquisition for Bot Framework service calls
    connection_manager: MsalConnectionManager | None = None

    if environ.get("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTH_TYPE", "").lower() == "usermanagedidentity":
        # Use User Managed Identity authentication
        connection_manager = MsalConnectionManager(**agents_sdk_config)  # pyright: ignore[reportUnknownArgumentType]

    # Create CloudAdapter with connection manager
    adapter = CloudAdapter(connection_manager=connection_manager)  # pyright: ignore[reportArgumentType]

    # Log authentication status
    bot_app_id = environ.get("BOT_ID")
    if bot_app_id:
        logger.info(f"🔐 Authentication configured for production (Bot ID: {bot_app_id[:8]}...)")
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
        context: TurnContext, state: WorkflowTurnState
    ) -> None:
        """Handle installation updates (e.g., bot installed)."""
        # Ensure state has our custom attributes
        if not hasattr(state, "pending_requests"):
            state.pending_requests = {}  # type: ignore
        if not hasattr(state, "workflow_output"):
            state.workflow_output = None  # type: ignore
        if not hasattr(state, "is_workflow_complete"):
            state.is_workflow_complete = False  # type: ignore
        # Installation events don't require a response
        pass

    @agent_app.activity("conversationUpdate")  # pyright: ignore[reportUnknownMemberType]
    async def _on_conversation_update(  # pyright: ignore[reportUnusedFunction]
        context: TurnContext, state: WorkflowTurnState
    ) -> None:
        """Handle conversation updates (e.g., member added)."""
        # Ensure state has our custom attributes (SDK may pass base TurnState)
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
        context: TurnContext, state: WorkflowTurnState
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
        context: TurnContext, state: WorkflowTurnState
    ) -> None:
        """
        Handle incoming message activities.

        This is the main entry point for processing user messages. It executes
        the event planning workflow and manages conversation state.
        """
        # Ensure state has our custom attributes (SDK may pass base TurnState)
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

        # Execute workflow
        await _execute_workflow(context, state, user_message)

    @agent_app.error
    async def _on_error(  # pyright: ignore[reportUnusedFunction]
        context: TurnContext, error: Exception
    ) -> None:
        """Handle uncaught errors."""
        logger.error(f"\n[on_error] Unhandled error: {error}", exc_info=True)

        await context.send_activity(
            "❌ **An unexpected error occurred.**\n\n"
            "The agent encountered an issue processing your request. "
            "Please try again or contact support if the problem persists."
        )

    # Start HTTP server (async call)
    logger.info("✅ Agent application ready!")

    # Get auth configuration from connection manager (standard pattern)
    auth_config = connection_manager.get_default_connection_configuration() if connection_manager else None

    await start_server(agent_app, auth_config)


def cli() -> None:
    """
    Create a synchronous entry point for the server command.

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
