# Copyright (c) Microsoft. All rights reserved.

"""Azure Storage operations for job queue and state management."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableServiceClient
from azure.storage.queue import QueueClient

from spec_to_agents.background_worker.models import JobSpec, JobState, to_dict

logger = logging.getLogger(__name__)

# Configuration from environment
TABLE_CONN_STR = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "AgentJobs")
QUEUE_NAME = os.environ.get("QUEUE_NAME", "agent-jobs")

# Module-level clients (initialized lazily)
_table_client = None
_queue_client = None


def _get_table_client():
    """Get or create table client (lazy initialization)."""
    global _table_client
    if _table_client is None and TABLE_CONN_STR:
        table_service = TableServiceClient.from_connection_string(TABLE_CONN_STR)
        _table_client = table_service.get_table_client(TABLE_NAME)
    return _table_client


def _get_queue_client():
    """Get or create queue client (lazy initialization)."""
    global _queue_client
    if _queue_client is None and TABLE_CONN_STR:
        _queue_client = QueueClient.from_connection_string(TABLE_CONN_STR, QUEUE_NAME)
    return _queue_client


def init_storage() -> None:
    """
    Initialize Azure Storage resources (table and queue).

    Creates table and queue if they don't exist. Safe to call multiple times.
    Requires AZURE_STORAGE_CONNECTION_STRING environment variable.

    Raises
    ------
    ValueError
        If AZURE_STORAGE_CONNECTION_STRING is not set
    """
    if not TABLE_CONN_STR:
        raise ValueError(
            "AZURE_STORAGE_CONNECTION_STRING environment variable is required for background worker"
        )

    table = _get_table_client()
    queue = _get_queue_client()

    if table is None or queue is None:
        raise ValueError("Failed to initialize storage clients")

    try:
        table.create_table()
        logger.info(f"✅ Created table: {TABLE_NAME}")
    except ResourceExistsError:
        logger.debug(f"Table already exists: {TABLE_NAME}")
    except Exception as e:
        logger.warning(f"⚠️  Could not create table {TABLE_NAME}: {e}")

    try:
        queue.create_queue()
        logger.info(f"✅ Created queue: {QUEUE_NAME}")
    except ResourceExistsError:
        logger.debug(f"Queue already exists: {QUEUE_NAME}")
    except Exception as e:
        logger.warning(f"⚠️  Could not create queue {QUEUE_NAME}: {e}")


def new_job(
    task: str,
    params: dict[str, Any],
    user_oid: str,
    tenant_id: str,
    conv_ref: dict[str, Any],
) -> JobSpec:
    """
    Create a new background job.

    Generates a unique job ID, creates initial state in table storage,
    and enqueues the job for processing.

    Parameters
    ----------
    task : str
        Description of the task to perform
    params : dict[str, Any]
        Task-specific parameters
    user_oid : str
        Azure AD object ID of the user
    tenant_id : str
        Tenant ID
    conv_ref : dict[str, Any]
        Conversation reference for proactive updates

    Returns
    -------
    JobSpec
        The created job specification

    Raises
    ------
    ValueError
        If storage is not initialized or operation fails
    """
    table = _get_table_client()
    queue = _get_queue_client()

    if table is None or queue is None:
        raise ValueError("Storage not initialized. Call init_storage() first.")

    job_id = str(uuid.uuid4())
    spec = JobSpec(
        job_id=job_id,
        task=task,
        params=params,
        user_aad_object_id=user_oid,
        tenant_id=tenant_id,
        conversation_reference=conv_ref,
        created_utc=datetime.now(timezone.utc),
    )

    # Create initial state row
    state = JobState(
        job_id=job_id,
        status="queued",
        percent=0,
        last_cursor=None,
        summary="Queued",
        updated_utc=datetime.now(timezone.utc),
        error=None,
    )

    try:
        # Store job spec and state in table
        entity = {"PartitionKey": "Job", "RowKey": job_id, **to_dict(state)}
        # Add spec fields for retrieval
        entity["task"] = task
        entity["params"] = json.dumps(params)
        entity["user_aad_object_id"] = user_oid
        entity["tenant_id"] = tenant_id
        entity["conversation_reference"] = json.dumps(conv_ref)
        entity["created_utc"] = spec.created_utc

        table.upsert_entity(entity)
        logger.info(f"📝 Created job state for {job_id}")

        # Enqueue work
        queue.send_message(json.dumps({"job_id": job_id}))
        logger.info(f"📨 Enqueued job {job_id}")

    except Exception as e:
        logger.error(f"❌ Failed to create job: {e}")
        raise ValueError(f"Failed to create job: {e}") from e

    return spec


def get_state(job_id: str) -> JobState | None:
    """
    Retrieve current state of a job.

    Parameters
    ----------
    job_id : str
        The job ID to look up

    Returns
    -------
    JobState | None
        The job state if found, None otherwise
    """
    table = _get_table_client()
    if table is None:
        return None

    try:
        entity = table.get_entity("Job", job_id)
        return JobState(
            job_id=entity["job_id"],
            status=entity["status"],
            percent=int(entity["percent"]),
            last_cursor=entity.get("last_cursor"),
            summary=entity.get("summary", ""),
            updated_utc=entity["updated_utc"],
            error=entity.get("error"),
        )
    except ResourceNotFoundError:
        logger.debug(f"Job {job_id} not found")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to get job state for {job_id}: {e}")
        return None


def update_state(job_id: str, **kwargs: Any) -> None:
    """
    Update job state fields.

    Parameters
    ----------
    job_id : str
        The job ID to update
    **kwargs : Any
        Fields to update (e.g., status="running", percent=50)

    Raises
    ------
    ValueError
        If job not found or update fails
    """
    table = _get_table_client()
    if table is None:
        raise ValueError("Storage not initialized")

    try:
        entity = table.get_entity("Job", job_id)
        for key, value in kwargs.items():
            entity[key] = value
        entity["updated_utc"] = datetime.now(timezone.utc)
        table.upsert_entity(entity)
        logger.debug(f"✅ Updated job {job_id}: {kwargs}")
    except ResourceNotFoundError:
        raise ValueError(f"Job {job_id} not found") from None
    except Exception as e:
        logger.error(f"❌ Failed to update job {job_id}: {e}")
        raise ValueError(f"Failed to update job: {e}") from e


def cancel_job(job_id: str) -> None:
    """
    Cancel a job.

    Parameters
    ----------
    job_id : str
        The job ID to cancel
    """
    update_state(job_id, status="cancelled", summary="Cancelled by user")
    logger.info(f"🛑 Cancelled job {job_id}")


def dequeue() -> str | None:
    """
    Dequeue and delete next job from queue.

    Returns
    -------
    str | None
        Job ID if a job was dequeued, None if queue is empty

    Notes
    -----
    This operation atomically retrieves and deletes a message from the queue
    with a 30-second visibility timeout. If processing takes longer than 30
    seconds, the message becomes visible again for retry.
    """
    queue = _get_queue_client()
    if queue is None:
        return None

    try:
        messages = queue.receive_messages(visibility_timeout=30, max_messages=1)
        for msg in messages:
            try:
                queue.delete_message(msg.id, msg.pop_receipt)
                body = json.loads(msg.content)
                job_id = body.get("job_id")
                if job_id:
                    logger.info(f"📦 Dequeued job {job_id}")
                    return str(job_id)
            except Exception as e:
                logger.error(f"❌ Failed to process queue message: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to dequeue: {e}")
        return None


def get_job_spec(job_id: str) -> JobSpec | None:
    """
    Retrieve full job specification from storage.

    Parameters
    ----------
    job_id : str
        The job ID to look up

    Returns
    -------
    JobSpec | None
        The job specification if found, None otherwise
    """
    table = _get_table_client()
    if table is None:
        return None

    try:
        entity = table.get_entity("Job", job_id)
        return JobSpec(
            job_id=entity["job_id"],
            user_aad_object_id=entity["user_aad_object_id"],
            tenant_id=entity["tenant_id"],
            conversation_reference=json.loads(entity["conversation_reference"]),
            task=entity["task"],
            params=json.loads(entity["params"]),
            created_utc=entity["created_utc"],
        )
    except ResourceNotFoundError:
        logger.debug(f"Job spec {job_id} not found")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to get job spec for {job_id}: {e}")
        return None
