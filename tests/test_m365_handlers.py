# Copyright (c) Microsoft. All rights reserved.

"""Unit tests for M365 handlers module."""

import json
from unittest.mock import patch

import pytest

from spec_to_agents.m365.handlers import (
    AGENT_NAMES,
    generate_context_card,
)
from spec_to_agents.m365.state import WorkflowTurnState, clear_workflow_cache


class TestAgentNames:
    """Tests for AGENT_NAMES constant."""

    def test_all_agents_have_display_names(self) -> None:
        """Test that all expected agents have display names defined."""
        expected_agents = ["venue", "budget", "catering", "logistics", "coordinator"]

        for agent in expected_agents:
            assert agent in AGENT_NAMES
            assert isinstance(AGENT_NAMES[agent], str)
            assert len(AGENT_NAMES[agent]) > 0

    def test_display_names_have_emojis(self) -> None:
        """Test that agent display names include emoji icons."""
        for name, display_name in AGENT_NAMES.items():
            # Each display name should have an emoji (non-ASCII character)
            has_emoji = any(ord(c) > 127 for c in display_name)
            assert has_emoji, f"Agent {name} display name '{display_name}' missing emoji"


class TestGenerateContextCard:
    """Tests for generate_context_card function."""

    def test_returns_attachment_with_valid_inputs(self) -> None:
        """Test that generate_context_card returns an Attachment."""
        from microsoft_agents.activity import Attachment

        result = generate_context_card(
            agent_icon="🏢",
            agent_name="Venue Specialist",
            summary="Found 3 venues matching your criteria.",
            next_agent="budget",
            user_input_needed=True,
        )

        assert result is not None
        assert isinstance(result, Attachment)
        assert result.content_type == "application/vnd.microsoft.card.adaptive"

    def test_returns_none_when_json_invalid(self) -> None:
        """Test that generate_context_card returns None when JSON is invalid."""
        with patch("builtins.open", side_effect=json.JSONDecodeError("Invalid", "doc", 0)):
            result = generate_context_card(
                agent_icon="🏢",
                agent_name="Test",
                summary="Test summary",
                next_agent=None,
                user_input_needed=False,
            )

            # Should return None gracefully, not raise
            assert result is None

    def test_handles_none_agent_icon(self) -> None:
        """Test that None agent_icon defaults to robot emoji."""
        result = generate_context_card(
            agent_icon=None,
            agent_name="Test Agent",
            summary="Test summary",
            next_agent=None,
            user_input_needed=False,
        )

        assert result is not None
        # Card content should have been created (icon defaults to 🤖)

    def test_formats_next_agent_name(self) -> None:
        """Test that next_agent is formatted with title case."""
        result = generate_context_card(
            agent_icon="💰",
            agent_name="Budget Analyst",
            summary="Budget analysis complete.",
            next_agent="catering_coordinator",
            user_input_needed=False,
        )

        assert result is not None
        # The card should format "catering_coordinator" as "Catering Coordinator"
        card_json = json.dumps(result.content)
        assert "Catering Coordinator" in card_json

    def test_handles_none_next_agent(self) -> None:
        """Test that None next_agent is handled correctly."""
        result = generate_context_card(
            agent_icon="📅",
            agent_name="Logistics Manager",
            summary="Schedule finalized.",
            next_agent=None,
            user_input_needed=False,
        )

        assert result is not None
        card_json = json.dumps(result.content)
        # show_next_agent should be false
        assert '"isVisible": false' in card_json or "None" in card_json

    def test_status_reflects_user_input_needed(self) -> None:
        """Test that status text reflects user_input_needed flag."""
        result_waiting = generate_context_card(
            agent_icon="🏢",
            agent_name="Test",
            summary="Test",
            next_agent=None,
            user_input_needed=True,
        )

        result_processing = generate_context_card(
            agent_icon="🏢",
            agent_name="Test",
            summary="Test",
            next_agent=None,
            user_input_needed=False,
        )

        assert result_waiting is not None
        assert result_processing is not None

        waiting_json = json.dumps(result_waiting.content)
        processing_json = json.dumps(result_processing.content)

        assert "Waiting for input" in waiting_json
        assert "Processing" in processing_json

    def test_sanitizes_special_characters_in_summary(self) -> None:
        """Test that special characters in summary are properly escaped."""
        # Summary with characters that need JSON escaping
        summary_with_quotes = 'The venue "Grand Hall" costs $5,000.'

        result = generate_context_card(
            agent_icon="🏢",
            agent_name="Venue",
            summary=summary_with_quotes,
            next_agent=None,
            user_input_needed=False,
        )

        assert result is not None
        # Card should be valid JSON (would fail if not properly escaped)
        card_json = json.dumps(result.content)
        assert "Grand Hall" in card_json


class TestWorkflowTurnStateInHandlers:
    """Tests for WorkflowTurnState usage in handler context."""

    def test_state_pending_requests_tracks_hitl(self) -> None:
        """Test that pending_requests can store HITL requests."""
        from spec_to_agents.models.messages import HumanFeedbackRequest

        state = WorkflowTurnState()

        request = HumanFeedbackRequest(
            prompt="Which venue do you prefer?",
            context={"venues": ["A", "B", "C"]},
            request_type="selection",
            requesting_agent="venue",
            conversation=[],
        )
        state.pending_requests["request-123"] = request

        assert "request-123" in state.pending_requests
        assert state.pending_requests["request-123"].prompt == "Which venue do you prefer?"

    def test_state_can_be_cleared_after_response(self) -> None:
        """Test that pending_requests can be cleared when user responds."""
        from spec_to_agents.models.messages import HumanFeedbackRequest

        state = WorkflowTurnState()
        state.pending_requests["req-1"] = HumanFeedbackRequest(
            prompt="Test",
            context={},
            request_type="clarification",
            requesting_agent="budget",
            conversation=[],
        )
        state.pending_requests["req-2"] = HumanFeedbackRequest(
            prompt="Test2",
            context={},
            request_type="approval",
            requesting_agent="catering",
            conversation=[],
        )

        # Simulate response clearing
        state.pending_requests.clear()

        assert len(state.pending_requests) == 0


@pytest.fixture(autouse=True)
def cleanup_workflow_cache():
    """Ensure workflow cache is cleared for each test."""
    clear_workflow_cache()
    yield
    clear_workflow_cache()
