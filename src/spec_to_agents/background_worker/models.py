# Copyright (c) Microsoft. All rights reserved.

"""Data models for background worker job management."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class JobSpec:
    """
    Specification for a background job.

    Parameters
    ----------
    job_id : str
        Unique identifier for the job
    user_aad_object_id : str
        Azure AD object ID of the user who created the job
    tenant_id : str
        Tenant ID for the organization
    conversation_reference : dict[str, Any]
        Conversation reference for proactive updates (Teams/Copilot)
    task : str
        Description of the task to be performed
    params : dict[str, Any]
        Task-specific parameters
    created_utc : datetime
        Timestamp when the job was created (UTC)
    """

    job_id: str
    user_aad_object_id: str
    tenant_id: str
    conversation_reference: dict[str, Any]
    task: str
    params: dict[str, Any]
    created_utc: datetime


@dataclass
class JobState:
    """
    State of a background job including progress and checkpointing.

    Parameters
    ----------
    job_id : str
        Unique identifier for the job
    status : str
        Current status: 'queued', 'running', 'succeeded', 'failed', or 'cancelled'
    percent : int
        Progress percentage (0-100)
    last_cursor : str | None
        Checkpoint cursor for resumable execution (e.g., page token, record id)
    summary : str
        Human-readable summary of current progress
    updated_utc : datetime
        Timestamp of last update (UTC)
    error : str | None
        Error message if status is 'failed'
    """

    job_id: str
    status: str
    percent: int
    last_cursor: str | None
    summary: str
    updated_utc: datetime
    error: str | None = None


def to_dict(obj: Any) -> dict[str, Any]:
    """
    Convert dataclass to dictionary.

    Parameters
    ----------
    obj : Any
        Object to convert (typically a dataclass instance)

    Returns
    -------
    dict[str, Any]
        Dictionary representation of the object
    """
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return {"value": obj}
