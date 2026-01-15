# Copyright (c) Microsoft. All rights reserved.

"""Proactive messaging for background worker updates."""

import logging
from typing import Any

from microsoft_agents.activity import Activity, Attachment, ConversationReference
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import TurnContext

from spec_to_agents.background_worker.cards import final_card, progress_card

logger = logging.getLogger(__name__)


async def send_progress(
    adapter: CloudAdapter,
    conv_ref_dict: dict[str, Any],
    bot_app_id: str,
    job_id: str,
    percent: int,
    summary: str,
) -> None:
    """
    Send a proactive progress update to the user.

    Uses the CloudAdapter to continue a conversation and send a progress card.

    Parameters
    ----------
    adapter : CloudAdapter
        The CloudAdapter instance for sending messages
    conv_ref_dict : dict[str, Any]
        Conversation reference dictionary from TurnContext
    bot_app_id : str
        Bot application ID for authentication
    job_id : str
        The job ID being tracked
    percent : int
        Progress percentage (0-100)
    summary : str
        Human-readable progress summary
    """
    try:
        # Convert dict to ConversationReference
        conv_ref = ConversationReference(**conv_ref_dict)

        # Define callback to send progress message
        async def logic(turn_context: TurnContext) -> None:
            card = progress_card(job_id, percent, summary)
            att = Attachment(content_type="application/vnd.microsoft.card.adaptive", content=card)
            activity = Activity(type="message", attachments=[att])
            await turn_context.send_activity(activity)

        # Continue conversation proactively
        await adapter.continue_conversation(conv_ref, logic, bot_app_id)
        logger.info(f"📨 Sent progress update for job {job_id}: {percent}%")

    except Exception as e:
        logger.warning(f"⚠️  Failed to send progress update for job {job_id}: {e}")


async def send_final(
    adapter: CloudAdapter,
    conv_ref_dict: dict[str, Any],
    bot_app_id: str,
    status: str,
    summary: str,
    url: str | None = None,
) -> None:
    """
    Send a proactive final result message to the user.

    Uses the CloudAdapter to continue a conversation and send a completion card.

    Parameters
    ----------
    adapter : CloudAdapter
        The CloudAdapter instance for sending messages
    conv_ref_dict : dict[str, Any]
        Conversation reference dictionary from TurnContext
    bot_app_id : str
        Bot application ID for authentication
    status : str
        Final job status ('succeeded', 'failed', 'cancelled')
    summary : str
        Human-readable summary of the result
    url : str | None, optional
        URL for downloading results (if applicable)
    """
    try:
        # Convert dict to ConversationReference
        conv_ref = ConversationReference(**conv_ref_dict)

        # Define callback to send final message
        async def logic(turn_context: TurnContext) -> None:
            card = final_card(status, summary, url)
            att = Attachment(content_type="application/vnd.microsoft.card.adaptive", content=card)
            activity = Activity(type="message", attachments=[att])
            await turn_context.send_activity(activity)

        # Continue conversation proactively
        await adapter.continue_conversation(conv_ref, logic, bot_app_id)
        logger.info(f"✅ Sent final result with status: {status}")

    except Exception as e:
        logger.error(f"❌ Failed to send final result: {e}")
