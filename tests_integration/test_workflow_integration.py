# Copyright (c) Microsoft. All rights reserved.

"""Integration tests for the event planning workflow.

Test organization:
- Fast tests (~2-5s): Individual agent streaming tests that stop after first sentence
- Slow tests (~60-90s): Full workflow tests that run all agents end-to-end

Run fast tests only (default):
    uv run pytest tests_integration/ -v

Run all tests including slow:
    uv run pytest tests_integration/ -v --run-slow
"""

import os

import pytest
from agent_framework import ChatAgent

from spec_to_agents.agents import (
    budget_analyst,
    catering_coordinator,
    event_coordinator,
    logistics_manager,
    venue_specialist,
)
from spec_to_agents.workflow.core import build_event_planning_workflow


def skip_if_no_azure():
    """Skip test if Azure credentials are not configured."""
    if not os.getenv("AZURE_AI_PROJECT_ENDPOINT"):
        pytest.skip("Azure credentials not configured")


async def stream_until_sentence(agent: ChatAgent, prompt: str, keywords: list[str], max_chars: int = 500) -> str:
    """
    Stream agent response and stop after first complete sentence containing keywords.

    This dramatically speeds up integration tests by not waiting for full responses.

    Parameters
    ----------
    agent : ChatAgent
        The agent to query
    prompt : str
        The prompt to send
    keywords : list[str]
        Keywords to look for (at least one must be present)
    max_chars : int
        Maximum characters to collect before stopping

    Returns
    -------
    str
        The collected response text
    """
    collected: str = ""
    # sentence_end = re.compile(r"[.!?]\s*$")

    stream = agent.run_stream(prompt)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    try:
        async for update in stream:  # pyright: ignore[reportUnknownVariableType]
            if update.text:  # pyright: ignore[reportUnknownMemberType]
                collected += str(update.text)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

                # Stop if we have a complete sentence with keywords
                collected_lower = collected.lower()
                has_keyword = any(kw in collected_lower for kw in keywords)
                # has_sentence = sentence_end.search(collected) is not None

                if has_keyword:
                    break

                # Safety limit
                if len(collected) > max_chars:
                    break
    finally:
        # Properly close the async generator to avoid unclosed session warnings
        await stream.aclose()  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]

    return collected


# =============================================================================
# Fast Tests: Individual Agent Streaming Integration (~2-5s each)
# =============================================================================


@pytest.mark.asyncio
async def test_venue_specialist_responds(load_integration_env, setup_di_container):  # type: ignore
    """Test that venue specialist agent can respond to a simple query."""
    skip_if_no_azure()

    agent = venue_specialist.create_agent()
    result = await stream_until_sentence(
        agent,
        "Suggest 2 venue types for a 50-person corporate event",
        keywords=["venue", "space", "location", "room", "hotel", "center"],
    )

    assert result is not None
    assert len(result) > 0
    result_lower = result.lower()
    assert any(word in result_lower for word in ["venue", "space", "location", "room", "hotel", "center"])


@pytest.mark.asyncio
async def test_budget_analyst_responds(load_integration_env, setup_di_container):  # type: ignore
    """Test that budget analyst agent can respond to a simple query."""
    skip_if_no_azure()

    agent = budget_analyst.create_agent()
    result = await stream_until_sentence(
        agent,
        "Create a simple budget breakdown for $5000 event budget",
        keywords=["budget", "cost", "allocation", "$", "expense"],
    )

    assert result is not None
    assert len(result) > 0
    result_lower = result.lower()
    assert any(word in result_lower for word in ["budget", "cost", "allocation", "$", "expense"])


@pytest.mark.asyncio
async def test_catering_coordinator_responds(load_integration_env, setup_di_container):  # type: ignore
    """Test that catering coordinator agent can respond to a simple query."""
    skip_if_no_azure()

    agent = catering_coordinator.create_agent()
    result = await stream_until_sentence(
        agent,
        "Suggest catering options for 30 people at a lunch event",
        keywords=["food", "catering", "menu", "meal", "lunch"],
    )

    assert result is not None
    assert len(result) > 0
    result_lower = result.lower()
    assert any(word in result_lower for word in ["food", "catering", "menu", "meal", "lunch"])


@pytest.mark.asyncio
async def test_logistics_manager_responds(load_integration_env, setup_di_container):  # type: ignore
    """Test that logistics manager agent can respond to a simple query."""
    skip_if_no_azure()

    agent = logistics_manager.create_agent()
    result = await stream_until_sentence(
        agent,
        "What are key logistics considerations for a corporate event?",
        keywords=["logistics", "schedule", "timeline", "coordination", "plan"],
    )

    assert result is not None
    assert len(result) > 0
    result_lower = result.lower()
    assert any(word in result_lower for word in ["logistics", "schedule", "timeline", "coordination", "plan"])


@pytest.mark.asyncio
async def test_event_coordinator_responds(load_integration_env, setup_di_container):  # type: ignore
    """Test that event coordinator agent can respond to a simple query."""
    skip_if_no_azure()

    agent = event_coordinator.create_agent()
    result = await stream_until_sentence(
        agent,
        "What are the main steps to plan a corporate event?",
        keywords=["plan", "event", "coordinate", "step", "organize"],
    )

    assert result is not None
    assert len(result) > 0
    result_lower = result.lower()
    assert any(word in result_lower for word in ["plan", "event", "coordinate", "step", "organize"])


# =============================================================================
# Slow Tests: Full Workflow Integration (~60-90s each)
# These test the complete multi-agent orchestration
# =============================================================================


@pytest.mark.slow
@pytest.mark.asyncio
async def test_workflow_execution_basic(load_integration_env, setup_di_container):  # type: ignore
    """Test basic workflow execution with a simple event planning request."""
    skip_if_no_azure()

    workflow = build_event_planning_workflow()

    # Submit a test event planning request
    request = "Plan a corporate holiday party for 50 people with a budget of $5000"

    # Execute workflow
    result = await workflow.run(request)

    # Validate that result is generated
    assert result is not None
    assert len(result) > 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_workflow_execution_contains_sections(load_integration_env, setup_di_container):  # type: ignore
    """Test that workflow output contains expected sections from all agents."""
    skip_if_no_azure()

    workflow = build_event_planning_workflow()

    # Submit a test event planning request
    request = "Plan a team building event for 30 people in Seattle with a budget of $3000"

    # Execute workflow
    result = await workflow.run(request)

    # Convert result to lowercase for easier searching
    result_lower = str(result).lower()

    # Validate that result contains contributions from all specialists
    # These are flexible checks since exact wording may vary
    assert any(keyword in result_lower for keyword in ["venue", "location", "space"]), (
        "Result should contain venue information"
    )

    assert any(keyword in result_lower for keyword in ["budget", "cost", "allocation", "expense"]), (
        "Result should contain budget information"
    )

    assert any(keyword in result_lower for keyword in ["catering", "food", "menu", "beverage"]), (
        "Result should contain catering information"
    )

    assert any(keyword in result_lower for keyword in ["logistics", "timeline", "schedule"]), (
        "Result should contain logistics information"
    )
