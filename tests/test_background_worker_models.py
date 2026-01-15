# Copyright (c) Microsoft. All rights reserved.

"""Tests for background worker models."""

from datetime import datetime, timezone

from spec_to_agents.background_worker.models import JobSpec, JobState, to_dict


def test_job_spec_creation():
    """Test JobSpec dataclass creation."""
    now = datetime.now(timezone.utc)
    spec = JobSpec(
        job_id="test-123",
        user_aad_object_id="user-456",
        tenant_id="tenant-789",
        conversation_reference={"test": "ref"},
        task="Plan an event",
        params={"budget": 1000},
        created_utc=now,
    )

    assert spec.job_id == "test-123"
    assert spec.user_aad_object_id == "user-456"
    assert spec.tenant_id == "tenant-789"
    assert spec.conversation_reference == {"test": "ref"}
    assert spec.task == "Plan an event"
    assert spec.params == {"budget": 1000}
    assert spec.created_utc == now


def test_job_state_creation():
    """Test JobState dataclass creation."""
    now = datetime.now(timezone.utc)
    state = JobState(
        job_id="test-123",
        status="running",
        percent=50,
        last_cursor="cursor-abc",
        summary="Processing...",
        updated_utc=now,
        error=None,
    )

    assert state.job_id == "test-123"
    assert state.status == "running"
    assert state.percent == 50
    assert state.last_cursor == "cursor-abc"
    assert state.summary == "Processing..."
    assert state.updated_utc == now
    assert state.error is None


def test_job_state_with_error():
    """Test JobState with error."""
    now = datetime.now(timezone.utc)
    state = JobState(
        job_id="test-123",
        status="failed",
        percent=30,
        last_cursor=None,
        summary="Failed",
        updated_utc=now,
        error="Something went wrong",
    )

    assert state.status == "failed"
    assert state.error == "Something went wrong"


def test_to_dict():
    """Test to_dict helper function."""
    now = datetime.now(timezone.utc)
    state = JobState(
        job_id="test-123",
        status="queued",
        percent=0,
        last_cursor=None,
        summary="Queued",
        updated_utc=now,
    )

    result = to_dict(state)

    assert isinstance(result, dict)
    assert result["job_id"] == "test-123"
    assert result["status"] == "queued"
    assert result["percent"] == 0
    assert result["summary"] == "Queued"


def test_to_dict_with_non_dataclass():
    """Test to_dict with non-dataclass object."""
    result = to_dict("simple_string")
    assert result == {"value": "simple_string"}
