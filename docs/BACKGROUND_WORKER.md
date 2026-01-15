# Background Worker for Long-Running Workflows

This document describes the background worker pattern implemented to handle long-running agent workflows without timeout issues.

## Problem

The M365 Agents SDK server has timeout constraints:
- **Streaming timeouts**: M365 Copilot expects responses within ~20-30 seconds
- **Long-running workflows**: Agent workflows can take several minutes to complete
- **Connection failures**: Long-running streams cause connection failures and poor UX

## Solution

The background worker pattern offloads long-running workflows to asynchronous background processing:

1. **Job Queue**: Requests are enqueued in Azure Storage Queue
2. **State Management**: Job state and progress tracked in Azure Table Storage
3. **Proactive Updates**: Progress sent via proactive messages (no long streams)
4. **Adaptive Cards**: Interactive cards with auto-refresh for progress tracking
5. **Time-Bounded Chunks**: Workflow execution in time-bounded chunks with checkpointing

## Configuration

### Environment Variables

```bash
# Enable background worker mode
USE_JOB_QUEUE=true
ENABLE_BACKGROUND_WORKER=true

# Azure Storage connection string
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...

# Optional: Customize storage names
TABLE_NAME=AgentJobs
QUEUE_NAME=agent-jobs

# Optional: Worker tuning
WORKER_POLL_INTERVAL=5        # Seconds between queue polls
PROGRESS_UPDATE_INTERVAL=30   # Seconds between progress updates
TIME_BUDGET_PER_CHUNK=25      # Seconds per work chunk
```

## Usage

### Mode 1: Synchronous (Default)

Direct execution with timeout risk:
```bash
USE_JOB_QUEUE=false
ENABLE_BACKGROUND_WORKER=false
```

**Pros**: Simple, no external dependencies
**Cons**: Timeout issues for long workflows

### Mode 2: Asynchronous with Background Worker

Job queue with proactive updates:
```bash
USE_JOB_QUEUE=true
ENABLE_BACKGROUND_WORKER=true
AZURE_STORAGE_CONNECTION_STRING="..."
```

**Pros**: No timeouts, progress tracking, resumable
**Cons**: Requires Azure Storage, more complex

## Testing

Run tests:
```bash
uv run pytest tests/test_background_worker_*.py -v
```

See full documentation in the code modules for detailed API reference.
