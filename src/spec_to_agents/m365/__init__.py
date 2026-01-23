# Copyright (c) Microsoft. All rights reserved.

"""
Microsoft 365 Agents SDK integration module.

This module provides FastAPI-native integration for the Microsoft 365 Agents SDK,
enabling the event planning workflow to be exposed via the /api/messages endpoint.
"""

from .handlers import execute_workflow, generate_context_card
from .routes import create_m365_routes, initialize_m365_components
from .state import WorkflowTurnState, clear_workflow_cache, get_workflow_cache

__all__ = [
    "WorkflowTurnState",
    "clear_workflow_cache",
    "create_m365_routes",
    "execute_workflow",
    "generate_context_card",
    "get_workflow_cache",
    "initialize_m365_components",
]
