# Copyright (c) Microsoft. All rights reserved.

"""Unit tests for M365 state management module."""

import pytest

from spec_to_agents.m365.state import (
    WorkflowTurnState,
    clear_workflow_cache,
    get_workflow_cache,
)
from spec_to_agents.models.messages import HumanFeedbackRequest


class TestWorkflowTurnState:
    """Tests for WorkflowTurnState dataclass."""

    def test_default_initialization(self) -> None:
        """Test WorkflowTurnState initializes with correct defaults."""
        state = WorkflowTurnState()

        assert state.pending_requests == {}
        assert state.workflow_output is None
        assert state.is_workflow_complete is False

    def test_pending_requests_is_independent_per_instance(self) -> None:
        """
        Test that pending_requests dict is not shared between instances.

        This verifies the default_factory pattern is working correctly
        to avoid mutable default argument issues.
        """
        state1 = WorkflowTurnState()
        state2 = WorkflowTurnState()

        # Modify state1's pending_requests
        state1.pending_requests["test_id"] = HumanFeedbackRequest(
            prompt="test",
            context={},
            request_type="selection",
            requesting_agent="venue",
            conversation=[],
        )

        # state2 should be unaffected
        assert "test_id" not in state2.pending_requests
        assert len(state2.pending_requests) == 0

    def test_workflow_output_assignment(self) -> None:
        """Test setting workflow output."""
        state = WorkflowTurnState()
        state.workflow_output = "Final event plan..."

        assert state.workflow_output == "Final event plan..."

    def test_is_workflow_complete_flag(self) -> None:
        """Test workflow completion flag."""
        state = WorkflowTurnState()
        assert not state.is_workflow_complete

        state.is_workflow_complete = True
        assert state.is_workflow_complete


class TestWorkflowCache:
    """Tests for workflow cache functions."""

    def test_get_workflow_cache_returns_dict(self) -> None:
        """Test get_workflow_cache returns a dictionary."""
        cache = get_workflow_cache()
        assert isinstance(cache, dict)

    def test_get_workflow_cache_returns_same_instance(self) -> None:
        """Test get_workflow_cache returns the same global cache instance."""
        cache1 = get_workflow_cache()
        cache2 = get_workflow_cache()

        assert cache1 is cache2

    def test_clear_workflow_cache_empties_cache(self) -> None:
        """Test clear_workflow_cache removes all entries."""
        cache = get_workflow_cache()

        # Add a mock entry
        cache["test_conversation_id"] = "mock_workflow"  # type: ignore

        # Clear the cache
        clear_workflow_cache()

        # Verify cache is empty
        assert len(get_workflow_cache()) == 0

    def test_cache_persists_entries(self) -> None:
        """Test that cache entries persist across get_workflow_cache calls."""
        cache = get_workflow_cache()
        cache["conversation_123"] = "workflow_instance"  # type: ignore

        # Get cache again and verify entry exists
        cache2 = get_workflow_cache()
        assert "conversation_123" in cache2
        assert cache2["conversation_123"] == "workflow_instance"

        # Cleanup
        clear_workflow_cache()


@pytest.fixture(autouse=True)
def cleanup_cache():
    """Ensure cache is cleared before and after each test."""
    clear_workflow_cache()
    yield
    clear_workflow_cache()
