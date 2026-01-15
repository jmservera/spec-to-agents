# Copyright (c) Microsoft. All rights reserved.

"""Tests for background worker adaptive cards."""

from spec_to_agents.background_worker.cards import final_card, progress_card


def test_progress_card():
    """Test progress card generation."""
    card = progress_card(job_id="test-123", percent=50, summary="Working on it...")

    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.5"
    assert len(card["body"]) == 4
    assert card["body"][0]["text"] == "Working on your request"
    assert card["body"][1]["text"] == "Working on it..."
    assert "50%" in card["body"][2]["text"]
    assert card["body"][3]["value"] == 0.5

    # Check actions
    assert len(card["actions"]) == 2
    assert card["actions"][0]["verb"] == "continue"
    assert card["actions"][1]["verb"] == "cancel"
    assert card["actions"][1]["style"] == "destructive"

    # Check refresh
    assert "refresh" in card
    assert card["refresh"]["userIds"] == ["*"]
    assert card["refresh"]["action"]["data"]["jobId"] == "test-123"


def test_progress_card_zero_percent():
    """Test progress card with zero percent."""
    card = progress_card(job_id="test-456", percent=0, summary="Starting...")

    assert "0%" in card["body"][2]["text"]
    assert card["body"][3]["value"] == 0.0


def test_progress_card_hundred_percent():
    """Test progress card with hundred percent."""
    card = progress_card(job_id="test-789", percent=100, summary="Almost done...")

    assert "100%" in card["body"][2]["text"]
    assert card["body"][3]["value"] == 1.0


def test_final_card_succeeded():
    """Test final card for succeeded job."""
    card = final_card(status="succeeded", summary="All done!")

    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.5"
    assert len(card["body"]) == 2
    assert card["body"][0]["text"] == "Task complete"
    assert card["body"][1]["text"] == "All done!"
    assert len(card["actions"]) == 0


def test_final_card_failed():
    """Test final card for failed job."""
    card = final_card(status="failed", summary="Something went wrong")

    assert card["body"][0]["text"] == "Task finished"
    assert card["body"][1]["text"] == "Something went wrong"


def test_final_card_with_url():
    """Test final card with download URL."""
    card = final_card(
        status="succeeded",
        summary="Results ready",
        download_url="https://example.com/results.pdf",
    )

    assert len(card["actions"]) == 1
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert card["actions"][0]["title"] == "Open Result"
    assert card["actions"][0]["url"] == "https://example.com/results.pdf"


def test_final_card_cancelled():
    """Test final card for cancelled job."""
    card = final_card(status="cancelled", summary="Job was cancelled by user")

    assert card["body"][0]["text"] == "Task finished"
    assert card["body"][1]["text"] == "Job was cancelled by user"
