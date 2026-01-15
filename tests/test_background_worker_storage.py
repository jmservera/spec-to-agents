# Copyright (c) Microsoft. All rights reserved.

"""Tests for background worker storage module."""

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from spec_to_agents.background_worker.models import JobState
from spec_to_agents.background_worker.storage import (
    cancel_job,
    dequeue,
    get_state,
    init_storage,
    new_job,
    update_state,
)


@pytest.fixture
def mock_storage_clients():
    """Mock Azure Storage clients."""
    with patch("spec_to_agents.background_worker.storage._get_table_client") as mock_table, \
         patch("spec_to_agents.background_worker.storage._get_queue_client") as mock_queue:
        
        # Create mock clients
        table_client = MagicMock()
        queue_client = MagicMock()
        
        mock_table.return_value = table_client
        mock_queue.return_value = queue_client
        
        yield {
            "table": table_client,
            "queue": queue_client,
            "mock_table": mock_table,
            "mock_queue": mock_queue,
        }


def test_init_storage_success(mock_storage_clients):
    """Test successful storage initialization."""
    # Set environment variable
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    mock_storage_clients["table"].create_table.return_value = None
    mock_storage_clients["queue"].create_queue.return_value = None
    
    # Should not raise exception
    init_storage()
    
    mock_storage_clients["table"].create_table.assert_called_once()
    mock_storage_clients["queue"].create_queue.assert_called_once()
    
    # Clean up
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_init_storage_missing_connection_string():
    """Test storage initialization without connection string."""
    # Ensure env var is not set
    if "AZURE_STORAGE_CONNECTION_STRING" in os.environ:
        del os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    
    with pytest.raises(ValueError, match="AZURE_STORAGE_CONNECTION_STRING"):
        init_storage()


def test_new_job(mock_storage_clients):
    """Test creating a new job."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    mock_storage_clients["table"].upsert_entity.return_value = None
    mock_storage_clients["queue"].send_message.return_value = None
    
    spec = new_job(
        task="Plan event",
        params={"budget": 1000},
        user_oid="user-123",
        tenant_id="tenant-456",
        conv_ref={"test": "ref"},
    )
    
    assert spec.task == "Plan event"
    assert spec.params == {"budget": 1000}
    assert spec.user_aad_object_id == "user-123"
    assert spec.tenant_id == "tenant-456"
    
    mock_storage_clients["table"].upsert_entity.assert_called_once()
    mock_storage_clients["queue"].send_message.assert_called_once()
    
    # Check that job_id was sent in queue message
    call_args = mock_storage_clients["queue"].send_message.call_args[0][0]
    message_data = json.loads(call_args)
    assert "job_id" in message_data
    assert message_data["job_id"] == spec.job_id
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_get_state(mock_storage_clients):
    """Test retrieving job state."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    now = datetime.now(timezone.utc)
    mock_entity = {
        "job_id": "test-123",
        "status": "running",
        "percent": 50,
        "last_cursor": "cursor-abc",
        "summary": "Working...",
        "updated_utc": now,
        "error": None,
    }
    mock_storage_clients["table"].get_entity.return_value = mock_entity
    
    state = get_state("test-123")
    
    assert state is not None
    assert state.job_id == "test-123"
    assert state.status == "running"
    assert state.percent == 50
    assert state.last_cursor == "cursor-abc"
    
    mock_storage_clients["table"].get_entity.assert_called_once_with("Job", "test-123")
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_get_state_not_found(mock_storage_clients):
    """Test retrieving non-existent job state."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    from azure.core.exceptions import ResourceNotFoundError
    mock_storage_clients["table"].get_entity.side_effect = ResourceNotFoundError("Not found")
    
    state = get_state("nonexistent")
    
    assert state is None
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_update_state(mock_storage_clients):
    """Test updating job state."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    now = datetime.now(timezone.utc)
    mock_entity = {
        "PartitionKey": "Job",
        "RowKey": "test-123",
        "job_id": "test-123",
        "status": "running",
        "percent": 50,
        "summary": "Working...",
        "updated_utc": now,
    }
    mock_storage_clients["table"].get_entity.return_value = mock_entity
    mock_storage_clients["table"].upsert_entity.return_value = None
    
    update_state("test-123", status="succeeded", percent=100)
    
    mock_storage_clients["table"].get_entity.assert_called_once_with("Job", "test-123")
    mock_storage_clients["table"].upsert_entity.assert_called_once()
    
    # Check that the entity was updated
    updated_entity = mock_storage_clients["table"].upsert_entity.call_args[0][0]
    assert updated_entity["status"] == "succeeded"
    assert updated_entity["percent"] == 100
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_cancel_job(mock_storage_clients):
    """Test cancelling a job."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    now = datetime.now(timezone.utc)
    mock_entity = {
        "PartitionKey": "Job",
        "RowKey": "test-123",
        "job_id": "test-123",
        "status": "running",
        "percent": 50,
        "summary": "Working...",
        "updated_utc": now,
    }
    mock_storage_clients["table"].get_entity.return_value = mock_entity
    mock_storage_clients["table"].upsert_entity.return_value = None
    
    cancel_job("test-123")
    
    # Check that status was updated to cancelled
    updated_entity = mock_storage_clients["table"].upsert_entity.call_args[0][0]
    assert updated_entity["status"] == "cancelled"
    assert updated_entity["summary"] == "Cancelled by user"
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_dequeue(mock_storage_clients):
    """Test dequeuing a job."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    # Create mock message
    mock_message = Mock()
    mock_message.id = "msg-123"
    mock_message.pop_receipt = "receipt-456"
    mock_message.content = json.dumps({"job_id": "job-789"})
    
    mock_storage_clients["queue"].receive_messages.return_value = [mock_message]
    mock_storage_clients["queue"].delete_message.return_value = None
    
    job_id = dequeue()
    
    assert job_id == "job-789"
    mock_storage_clients["queue"].receive_messages.assert_called_once()
    mock_storage_clients["queue"].delete_message.assert_called_once_with("msg-123", "receipt-456")
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def test_dequeue_empty_queue(mock_storage_clients):
    """Test dequeuing from empty queue."""
    os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "test_connection_string"
    
    mock_storage_clients["queue"].receive_messages.return_value = []
    
    job_id = dequeue()
    
    assert job_id is None
    
    del os.environ["AZURE_STORAGE_CONNECTION_STRING"]
