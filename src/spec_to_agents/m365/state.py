# Copyright (c) Microsoft. All rights reserved.

"""
State management for Microsoft 365 Agents SDK integration.

This module provides shared state classes and caches used by both the M365
server endpoint and DevUI for maintaining conversation context across requests.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from microsoft_agents.hosting.core import TurnState as BaseTurnState

if TYPE_CHECKING:
    from agent_framework import Workflow

from spec_to_agents.models.messages import HumanFeedbackRequest


@dataclass
class WorkflowTurnState(BaseTurnState):
    """
    Extended turn state for tracking workflow execution across conversation turns.

    This state is preserved across HTTP requests within a conversation, enabling
    human-in-the-loop interactions and workflow continuity.

    Attributes
    ----------
    pending_requests : dict[str, HumanFeedbackRequest]
        Map of request_id to HumanFeedbackRequest for human-in-the-loop interactions.
        When the workflow needs user input, the request is stored here and the
        workflow pauses until the user responds.
    workflow_output : str | None
        Final output from completed workflow, typically the synthesized event plan.
    is_workflow_complete : bool
        Whether the workflow has finished execution. Used to determine if a new
        workflow run should be started or if results should be displayed.
    """

    pending_requests: dict[str, HumanFeedbackRequest] = field(
        default_factory=lambda: {}  # pyright: ignore[reportUnknownLambdaType]
    )
    workflow_output: str | None = None
    is_workflow_complete: bool = False


# Workflow instance cache to preserve agent service threads across HTTP requests
# Key: conversation_id, Value: workflow instance with agents
_workflow_cache: dict[str, "Workflow"] = {}


def get_workflow_cache() -> dict[str, "Workflow"]:
    """
    Get the global workflow cache.

    Returns the shared workflow cache dictionary that preserves workflow instances
    (and their agent service thread IDs) across HTTP requests within a conversation.

    Returns
    -------
    dict[str, Workflow]
        Dictionary mapping conversation IDs to workflow instances.
    """
    return _workflow_cache


def clear_workflow_cache() -> None:
    """
    Clear all cached workflow instances.

    This should be called during shutdown to ensure proper cleanup of resources.
    """
    _workflow_cache.clear()
