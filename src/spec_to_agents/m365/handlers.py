# Copyright (c) Microsoft. All rights reserved.

"""
Activity handlers for Microsoft 365 Agents SDK integration.

This module contains the workflow execution logic and activity handlers that
process incoming messages from Microsoft 365 channels (Teams, Copilot, etc.).
"""

import json
import logging
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from agent_framework import (
    AgentRunUpdateEvent,
    ExecutorCompletedEvent,
    RequestInfoEvent,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
)
from microsoft_agents.activity import Activity, ActivityTypes, Attachment
from microsoft_agents.hosting.core import TurnContext

from spec_to_agents.models.messages import HumanFeedbackRequest
from spec_to_agents.utils.copilot_display import AGENT_ICONS, get_agent_icon
from spec_to_agents.workflow.core import build_event_planning_workflow

from .state import WorkflowTurnState, get_workflow_cache

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Agent display names for user-friendly messages
AGENT_NAMES = {
    "venue": "🏢 Venue Specialist",
    "budget": "💰 Budget Analyst",
    "catering": "🍽️ Catering Coordinator",
    "logistics": "📅 Logistics Manager",
    "coordinator": "🎯 Event Coordinator",
}


def generate_context_card(
    agent_icon: str | None,
    agent_name: str,
    summary: str,
    next_agent: str | None,
    user_input_needed: bool,
) -> Attachment | None:
    """
    Generate an adaptive card showing the agent's current thinking context.

    Parameters
    ----------
    agent_icon : str | None
        Icon emoji for the agent
    agent_name : str
        The name of the agent requesting input
    summary : str
        Summary of the agent's current analysis/recommendations
    next_agent : str | None
        The next agent to consult (if any)
    user_input_needed : bool
        Whether user input is needed

    Returns
    -------
    Attachment | None
        Adaptive card attachment, or None if card generation fails.
    """
    try:
        # Load adaptive card template
        card_path = Path(__file__).parent.parent / "adaptive_cards" / "agent_context_card.json"
        with open(card_path, "r", encoding="utf-8") as f:
            card_template = json.load(f)

        # Format next agent
        formatted_next_agent = next_agent.replace("_", " ").title() if next_agent else "None"

        # Status based on user_input_needed
        status = "⏸️ Waiting for input" if user_input_needed else "✅ Processing"

        # Show next agent section only if next_agent is not None
        show_next_agent = next_agent is not None

        def sanitize_for_json(text: str) -> str:
            """Escape text for safe insertion into JSON string."""
            return json.dumps(text)[1:-1]

        # Replace template variables with sanitized values
        card_json = json.dumps(card_template)
        card_json = card_json.replace("${agent_icon}", sanitize_for_json(agent_icon or "🤖"))  # noqa: RUF027
        card_json = card_json.replace("${agent_name}", sanitize_for_json(agent_name))  # noqa: RUF027
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
        logger.warning(f"⚠️  Could not generate agent context card: {e}")
        logger.debug(traceback.format_exc())
    return None


async def execute_workflow(
    context: TurnContext,
    state: WorkflowTurnState,
    user_message: str,
) -> None:
    """
    Execute the event planning workflow and send responses.

    This function integrates the agent_framework workflow with the M365 SDK
    activity handler pattern. It processes streaming events from the workflow
    and converts them to activity messages.

    NOTE: Workflow instances are cached per conversation_id in the workflow cache
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
    last_notified_agent: str | None = None

    # Access streaming response - it may be None in non-streaming contexts
    streaming = context.streaming_response
    stream_timed_out = False
    streaming_stopped = False
    streamed_content: list[str] = []
    streamed_attachments: list[Attachment] = []
    already_queued_text = False

    def is_stream_alive() -> bool:
        """Check if the streaming response is still usable."""
        if not streaming or streaming_stopped or stream_timed_out:
            return False
        try:
            return not streaming._ended  # pyright: ignore[reportPrivateUsage]
        except Exception:
            return False

    async def queue_info_update(info: str | None) -> None:
        nonlocal stream_timed_out, last_progress_message_time, last_typing_time

        last_typing_time = time.time()

        if not already_queued_text:
            try:
                if is_stream_alive():
                    if info:
                        streaming.queue_informative_update(info)  # pyright: ignore[reportOptionalMemberAccess]
                    else:
                        await context.send_activity(Activity(type=ActivityTypes.typing))  # pyright: ignore[reportCallIssue]
                    logger.debug("📨 Sent progress update message")
                last_progress_message_time = time.time()
            except Exception as e:
                if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                    logger.warning("⚠️ Stream timed out during progress update")
                    stream_timed_out = True
                else:
                    logger.warning(f"⚠️  Could not send progress message: {e}")
        else:
            last_progress_message_time = time.time()
            logger.debug("ℹ️  Skipping progress update, text already queued")  # noqa: RUF001

    async def queue_attachment(attachment: Attachment) -> None:
        nonlocal stream_timed_out, already_queued_text

        streamed_attachments.append(attachment)

        try:
            if is_stream_alive():
                streaming.set_attachments(streamed_attachments)  # pyright: ignore[reportOptionalMemberAccess]
                logger.debug("📎 Queued attachment for streaming response")
            else:
                logger.debug("ℹ️  Stream not alive, sending attachment directly")  # noqa: RUF001
                already_queued_text = True
                await context.send_activity(
                    Activity(type="message", attachments=streamed_attachments)  # pyright: ignore[reportCallIssue]
                )
                streamed_attachments.clear()
        except Exception as e:
            if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                logger.warning("⚠️ Stream timed out during attachment queuing")
                stream_timed_out = True
            else:
                logger.warning(f"⚠️  Could not queue attachment: {e}")

    def queue_text_activity(text: str) -> None:
        nonlocal already_queued_text, stream_timed_out

        streamed_content.append(text)
        already_queued_text = True
        try:
            logger.debug("💬 Sending text activity...")
            if is_stream_alive():
                streaming.queue_text_chunk(text)  # pyright: ignore[reportOptionalMemberAccess]
            else:
                logger.debug("ℹ️  Stream not alive, caching text for later activity")  # noqa: RUF001
            logger.debug("✅ Text activity sent")
        except Exception as e:
            if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                logger.warning("⚠️ Stream timed out during text update, MESSAGE NOT SENT")
                stream_timed_out = True
            else:
                logger.warning(f"⚠️  Could not send text activity: {e}")

    async def flush_queue_and_end_stream() -> None:
        nonlocal stream_timed_out, streaming_stopped

        not_flushed = False
        activity: Activity | None = None
        try:
            if is_stream_alive():
                await streaming.end_stream()  # pyright: ignore[reportOptionalMemberAccess]
                streaming_stopped = True
                streamed_attachments.clear()
                streamed_content.clear()
                logger.debug("✅ Stream flushed and ended")
            else:
                not_flushed = True
                if not streaming_stopped:
                    logger.debug("⚠️ Stream not alive, could not flush/end")
        except Exception as e:
            if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                logger.warning("⚠️ Stream timed out during flush/end")
                stream_timed_out = True
            else:
                logger.warning(f"⚠️ Failed to flush/end stream: {e}")
            not_flushed = True

        if not_flushed:
            try:
                if len(streamed_attachments) > 0:
                    activity = Activity(  # pyright: ignore[reportCallIssue]
                        type="message",
                        text="".join(streamed_content),
                        attachments=streamed_attachments,
                    )
                else:
                    message = "".join(streamed_content)
                    if len(message) >= 0:
                        activity = Activity(type="message", text=message)  # pyright: ignore[reportCallIssue]
                if activity:
                    await context.send_activity(activity)
                    logger.debug("✅ Sent queued content as regular activity")
                streamed_attachments.clear()
                streamed_content.clear()
            except Exception as e:
                logger.error(f"❌ Failed to send queued content as activity: {e}")

    if streaming:
        logger.debug(f"🐞 Streaming enabled: {streaming._is_streaming_channel}")  # pyright: ignore[reportPrivateUsage]
        await queue_info_update("Starting workflow execution...")

    try:
        # Get or create workflow instance for this conversation
        conversation_id = context.activity.conversation.id
        workflow_cache = get_workflow_cache()

        if conversation_id in workflow_cache:
            workflow = workflow_cache[conversation_id]
            logger.info(f"♻️  Reusing cached workflow for conversation {conversation_id[:8]}...")
        else:
            workflow = build_event_planning_workflow()
            workflow_cache[conversation_id] = workflow
            logger.info(f"🆕 Created new workflow for conversation {conversation_id[:8]}...")

        # Check if responding to pending human-in-the-loop request
        if state.pending_requests:
            pending_responses = {request_id: user_message for request_id in state.pending_requests}
            state.pending_requests.clear()
            logger.info("🔄 Resuming from HITL")
            stream = workflow.send_responses_streaming(pending_responses)
        else:
            stream = workflow.run_stream(user_message)

        await queue_info_update("Starting workflow streaming...")

        try:
            async for event in stream:  # pyright: ignore[reportUnknownVariableType]
                if not is_stream_alive() and not streaming_stopped:
                    logger.warning("⚠️ Stream ended/timed out, continuing workflow without streaming...")

                current_time = time.time()

                if current_time - start_time >= 90.0 and not streaming_stopped:
                    logger.warning("⚠️ Workflow execution time exceeded 90 seconds, stopping updates")
                    try:
                        queue_text_activity(
                            "⏳ Still working on your event plan... I will send the final details shortly."
                        )
                        await flush_queue_and_end_stream()
                        logger.debug("✅ Stream proactively closed due to time limit")
                    except Exception as e:
                        if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                            stream_timed_out = True
                        else:
                            logger.warning(f"⚠️ Failed to end stream after time limit: {e}")

                elif current_time - last_progress_message_time >= 10.0:
                    await queue_info_update(
                        "⏳ Still working on your event plan... \n"
                        "This may take a moment as I coordinate with specialist agents."
                    )
                elif current_time - last_typing_time >= 5.0:
                    last_typing_time = current_time
                    await queue_info_update(None)

                # Handle agent run updates
                if isinstance(event, AgentRunUpdateEvent):
                    agent_data = event.data  # pyright: ignore[reportUnknownMemberType]
                    agent_name: str | None = None

                    if agent_data is not None and hasattr(agent_data, "author_name"):
                        author = getattr(agent_data, "author_name", None)
                        if author:
                            agent_name = str(author).lower()
                    elif isinstance(agent_data, dict) and "name" in agent_data:
                        agent_name = str(agent_data["name"]).lower()  # pyright: ignore[reportUnknownArgumentType]

                    if agent_name and agent_name != last_notified_agent:
                        agent_icon = get_agent_icon(event.executor_id)
                        display_name = AGENT_NAMES.get(
                            agent_name, f"{agent_icon} {agent_name.replace('_', ' ').title()}"
                        )
                        try:
                            message = f"Consulting with {display_name}..."
                            await queue_info_update(message)
                            logger.debug(f"👤 Notified user about agent: {display_name}")
                            last_notified_agent = agent_name
                        except Exception as e:
                            if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                                stream_timed_out = True
                            else:
                                logger.warning(f"⚠️  Could not send agent notification: {e}")

                # Handle human-in-the-loop requests
                elif isinstance(event, RequestInfoEvent) and isinstance(event.data, HumanFeedbackRequest):
                    feedback_request: HumanFeedbackRequest = event.data
                    state.pending_requests[event.request_id] = feedback_request

                    summary = None
                    next_agent = None
                    user_input_needed = True

                    if feedback_request.conversation and len(feedback_request.conversation) > 0:
                        last_message = feedback_request.conversation[-1]
                        try:
                            content_text = None
                            if hasattr(last_message, "text"):
                                content_text = last_message.text
                            if content_text:
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

                    requesting_agent_name = feedback_request.requesting_agent.title()
                    requesting_agent_icon = AGENT_ICONS.get(feedback_request.requesting_agent.lower(), "🤖")

                    if summary:
                        card = generate_context_card(
                            agent_icon=requesting_agent_icon,
                            agent_name=requesting_agent_name,
                            summary=summary,
                            next_agent=next_agent,
                            user_input_needed=user_input_needed,
                        )
                        if card:
                            if streaming and is_stream_alive():
                                text = "".join(streamed_content)
                                if len(text) == 0:
                                    source = event.source_executor_id.replace("_", " ").title()
                                    queue_text_activity(
                                        f"{get_agent_icon(event.source_executor_id)} {source} "
                                        f"delegated to {requesting_agent_icon} "
                                        f"{requesting_agent_name}."
                                    )
                            await queue_attachment(card)
                            await flush_queue_and_end_stream()

                    prompt_message = (
                        f"{requesting_agent_icon} **{requesting_agent_name} needs your input:**\n\n"
                        f"{feedback_request.prompt}"
                    )
                    queue_text_activity(prompt_message)
                    await flush_queue_and_end_stream()

                # Handle final workflow output
                elif isinstance(event, WorkflowOutputEvent):
                    state.workflow_output = str(event.data)
                    state.is_workflow_complete = True

                    try:
                        await flush_queue_and_end_stream()
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to end stream for workflow output: {e}")

                    output_text: str = state.workflow_output
                    try:
                        output_data = json.loads(state.workflow_output)
                        if isinstance(output_data, dict) and "summary" in output_data:
                            if isinstance(output_data["summary"], str):
                                output_text = str(output_data["summary"])
                            else:
                                output_text = json.dumps(output_data["summary"], indent=2)
                    except (json.JSONDecodeError, TypeError):
                        output_text = state.workflow_output

                    try:
                        await context.send_activity(f"**✨ Event Plan Complete:**\n\n{output_text}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send final output: {e}")

                elif isinstance(event, WorkflowStatusEvent):
                    pass  # Status events don't contain checkpoint info

                elif isinstance(event, ExecutorCompletedEvent):
                    pass  # Can be used in future for agent completion tracking

            # Loop completed - end stream if still alive
            try:
                if is_stream_alive():
                    logger.debug("✅ Stream ended after event loop completed")
                else:
                    logger.debug("ℹ️  Stream already ended after event loop")  # noqa: RUF001
                await flush_queue_and_end_stream()
            except Exception as e:
                if "exceeded streaming time" in str(e).lower() or "forbidden" in str(e).lower():
                    stream_timed_out = True
                else:
                    logger.warning(f"⚠️ Failed to end stream after loop: {e}")

        except Exception as stream_error:
            logger.error(f"⚠️ Error during stream iteration: {type(stream_error).__name__}: {stream_error!s}")
            logger.error(f"Stream error details:\n{traceback.format_exc()}")
            raise

    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"❌ Workflow execution failed: [{error_type}] {e!s}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")

        stream_ended_early = False

        try:
            await flush_queue_and_end_stream()
        except RuntimeError:
            stream_ended_early = True
            logger.warning("⚠️ Stream already ended, skipping end_stream() call")
        except Exception as end_error:
            if "exceeded streaming time" in str(end_error).lower() or "forbidden" in str(end_error).lower():
                stream_ended_early = True
                logger.warning("⚠️ Stream timed out during error handling")
            else:
                logger.warning(f"⚠️ Failed to end stream: {end_error}")

        error_message = f"❌ **Workflow execution interrupted:** {e!s}\n\n"

        if stream_ended_early and streamed_content:
            error_message += "**Partial results before interruption:**\n\n"
            error_message += "\n\n".join(str(c) for c in streamed_content)
            error_message += "\n\n---\n\n"
            logger.info(f"✅ Recovered {len(streamed_content)} content chunks from tracking")

        error_message += "Please try again or contact support if the issue persists."

        if stream_ended_early and streamed_attachments:
            activity = Activity(  # pyright: ignore[reportCallIssue]
                type="message",
                text=error_message,
                attachments=streamed_attachments,
            )
            logger.info(f"✅ Recovered {len(streamed_attachments)} attachments from tracking")
            await context.send_activity(activity)
        else:
            await context.send_activity(error_message)

        logger.error(f"Error executing workflow: {e}", exc_info=True)
        raise
