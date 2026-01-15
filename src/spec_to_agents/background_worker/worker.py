# Copyright (c) Microsoft. All rights reserved.

"""Background worker for processing long-running agent workflows."""

import asyncio
import logging
import os
import time
from typing import Any

from microsoft_agents.hosting.aiohttp import CloudAdapter

from spec_to_agents.background_worker import proactive, storage
from spec_to_agents.background_worker.models import JobState
from spec_to_agents.workflow.core import build_event_planning_workflow

logger = logging.getLogger(__name__)

# Configuration
WORKER_POLL_INTERVAL = int(os.environ.get("WORKER_POLL_INTERVAL", "5"))  # seconds
PROGRESS_UPDATE_INTERVAL = int(os.environ.get("PROGRESS_UPDATE_INTERVAL", "30"))  # seconds
TIME_BUDGET_PER_CHUNK = int(os.environ.get("TIME_BUDGET_PER_CHUNK", "25"))  # seconds


async def do_work_chunk(
    workflow: Any, user_message: str, cursor: str | None, params: dict[str, Any]
) -> tuple[str | None, int, str, str | None]:
    """
    Process a chunk of workflow execution with time budget.

    This function runs the workflow for a bounded time period, tracks progress,
    and returns a checkpoint cursor for resumption.

    Parameters
    ----------
    workflow : Any
        The workflow instance to execute
    user_message : str
        The user's request message
    cursor : str | None
        Checkpoint cursor from previous chunk (None for first chunk)
    params : dict[str, Any]
        Task-specific parameters

    Returns
    -------
    tuple[str | None, int, str, str | None]
        - next_cursor: Checkpoint for next chunk (None if complete)
        - percent: Progress percentage (0-100)
        - summary: Human-readable progress summary
        - result: Final result if workflow completed (None otherwise)

    Notes
    -----
    This is a simplified implementation. In production, you would:
    1. Track actual workflow progress through events
    2. Implement proper checkpointing of workflow state
    3. Handle resumption from checkpoints
    4. Implement chunked streaming with time budgets
    """
    start_time = time.time()

    try:
        # For now, we run the full workflow synchronously
        # In a production system, this would be chunked with checkpointing
        if cursor is None:
            # First chunk: start workflow
            stream = workflow.run_stream(user_message)
            
            # Process events with time budget
            result_text = None
            event_count = 0
            
            async for event in stream:
                event_count += 1
                elapsed = time.time() - start_time
                
                # Check time budget
                if elapsed > TIME_BUDGET_PER_CHUNK:
                    # Time budget exceeded, checkpoint and continue later
                    percent = min(50, event_count * 10)  # Rough progress estimate
                    summary = f"Processing event planning workflow... ({event_count} events processed)"
                    next_cursor = f"event_{event_count}"
                    return next_cursor, percent, summary, None
                
                # Check for workflow output
                from agent_framework import WorkflowOutputEvent
                if isinstance(event, WorkflowOutputEvent):
                    result_text = str(event.data)
                    break
            
            # Workflow completed in one chunk
            if result_text:
                return None, 100, "Event plan completed", result_text
            else:
                # Workflow finished but no output
                return None, 100, "Workflow completed", "No output generated"
        else:
            # Resuming from checkpoint (simplified - would need proper state restoration)
            # For now, just return completion
            return None, 100, "Resumed and completed", "Workflow resumed from checkpoint"
            
    except Exception as e:
        logger.error(f"❌ Error in work chunk: {e}", exc_info=True)
        raise


async def process_job(job_id: str, adapter: CloudAdapter, bot_app_id: str) -> None:
    """
    Process a background job by executing the workflow in chunks.

    This function retrieves the job specification, executes the workflow
    in time-bounded chunks, updates progress, and sends proactive messages.

    Parameters
    ----------
    job_id : str
        The job ID to process
    adapter : CloudAdapter
        CloudAdapter for sending proactive messages
    bot_app_id : str
        Bot application ID for authentication
    """
    logger.info(f"🚀 Processing job {job_id}")

    # Retrieve job specification
    spec = storage.get_job_spec(job_id)
    if spec is None:
        logger.error(f"❌ Job {job_id} not found")
        return

    # Get current state
    state = storage.get_state(job_id)
    if state is None:
        logger.error(f"❌ Job state {job_id} not found")
        return

    # Check if already completed or cancelled
    if state.status in ("succeeded", "failed", "cancelled"):
        logger.info(f"ℹ️  Job {job_id} already in terminal state: {state.status}")
        return

    # Update status to running
    storage.update_state(job_id, status="running", summary="Starting workflow execution...")

    # Send initial progress update
    try:
        await proactive.send_progress(
            adapter, spec.conversation_reference, bot_app_id, job_id, 0, "Starting workflow execution..."
        )
    except Exception as e:
        logger.warning(f"⚠️  Could not send initial progress: {e}")

    try:
        # Build workflow instance
        workflow = build_event_planning_workflow()
        cursor = state.last_cursor
        last_progress_time = time.time()

        # Process in chunks with checkpointing
        while True:
            # Check for cancellation
            current_state = storage.get_state(job_id)
            if current_state and current_state.status == "cancelled":
                logger.info(f"🛑 Job {job_id} was cancelled")
                await proactive.send_final(
                    adapter, spec.conversation_reference, bot_app_id, "cancelled", "Job was cancelled by user."
                )
                return

            # Execute work chunk
            cursor, percent, summary, result = await do_work_chunk(
                workflow, spec.task, cursor, spec.params
            )

            # Update state with checkpoint
            storage.update_state(job_id, last_cursor=cursor, percent=percent, summary=summary, status="running")

            # Send progress update if enough time has passed
            current_time = time.time()
            if current_time - last_progress_time >= PROGRESS_UPDATE_INTERVAL:
                try:
                    await proactive.send_progress(
                        adapter, spec.conversation_reference, bot_app_id, job_id, percent, summary
                    )
                    last_progress_time = current_time
                except Exception as e:
                    logger.warning(f"⚠️  Could not send progress update: {e}")

            # Check if complete
            if cursor is None:
                # Workflow complete
                storage.update_state(job_id, status="succeeded", percent=100, summary="Completed")
                await proactive.send_final(
                    adapter,
                    spec.conversation_reference,
                    bot_app_id,
                    "succeeded",
                    result or "Event plan completed successfully.",
                )
                logger.info(f"✅ Job {job_id} completed successfully")
                return

    except Exception as ex:
        # Job failed
        error_msg = f"Error: {ex}"
        storage.update_state(job_id, status="failed", error=error_msg, summary="Failed")
        try:
            await proactive.send_final(
                adapter, spec.conversation_reference, bot_app_id, "failed", f"Job failed: {ex}"
            )
        except Exception as e:
            logger.error(f"❌ Failed to send failure notification: {e}")
        logger.error(f"❌ Job {job_id} failed: {ex}", exc_info=True)


async def worker_loop(adapter: CloudAdapter, bot_app_id: str) -> None:
    """
    Main worker loop that polls queue and processes jobs.

    Parameters
    ----------
    adapter : CloudAdapter
        CloudAdapter for sending proactive messages
    bot_app_id : str
        Bot application ID for authentication
    """
    logger.info("🔄 Starting background worker loop...")

    while True:
        try:
            # Check for new jobs
            job_id = storage.dequeue()

            if job_id:
                # Process job
                await process_job(job_id, adapter, bot_app_id)
            else:
                # No jobs available, wait before polling again
                await asyncio.sleep(WORKER_POLL_INTERVAL)

        except Exception as e:
            logger.error(f"❌ Worker loop error: {e}", exc_info=True)
            await asyncio.sleep(WORKER_POLL_INTERVAL)
