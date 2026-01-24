# Microsoft 365 Agents SDK Server

This document describes how to use the Microsoft 365 Agents SDK HTTP server entrypoint (`m365_server.py`) for the event planning multi-agent workflow.

## Overview

The M365 server exposes the event planning workflow as an HTTP endpoint compatible with:
- Microsoft Teams
- Microsoft 365 Copilot
- Copilot Studio
- Azure Bot Service
- Web Chat and other channels

## Architecture

The server uses the **Microsoft 365 Agents SDK for Python** (`microsoft-agents-hosting`) to provide:

1. **Activity-Based Messaging**: Handles incoming messages via the Bot Framework Activity protocol
2. **Turn-Based Conversations**: Manages state across conversation turns
3. **HTTP Endpoint**: Exposes `/api/messages` for receiving activities
4. **Workflow Integration**: Executes the existing `agent_framework` workflow within activity handlers

### Key Components

```python
# Core M365 SDK imports
from microsoft_agents.hosting.core import (
    AgentApplication,      # Main application class with fluent API
    TurnContext,          # Context for each conversation turn
    TurnState,            # State management across turns
    MemoryStorage,        # In-memory storage for development
    CloudAdapter,         # Handles channel communication
)

from microsoft_agents.hosting.aiohttp import (
    start_agent_process,  # Process incoming activities
    jwt_authorization_middleware,  # JWT validation
)
```

### Workflow Integration

The server bridges the streaming workflow from `agent_framework` with the turn-based M365 SDK:

```
User Message (via Teams/Copilot)
    ↓
Activity Handler (@agent_app.activity("message"))
    ↓
Execute Workflow (workflow.run_stream())
    ↓
Process Streaming Events
    ├─ AgentRunUpdateEvent → (logged/ignored)
    ├─ RequestInfoEvent → Send prompt via send_activity()
    ├─ WorkflowOutputEvent → Send final plan via send_activity()
    └─ WorkflowStatusEvent → (logged/ignored)
    ↓
Response sent back to channel
```

## Installation

The M365 SDK packages are automatically installed when you run:

```bash
uv sync
```

The following packages are added to `pyproject.toml`:
- `microsoft-agents-hosting-core>=0.6.0` - Core hosting library
- `microsoft-agents-hosting-aiohttp>=0.6.0` - aiohttp integration

## Running the Server

### Development (Local)

```bash
uv run m365-server
```

This starts an HTTP server on `http://localhost:3978` with:
- **Endpoint**: `POST /api/messages` (receives Bot Framework activities)
- **Health Check**: `GET /api/messages` (returns 200 OK)

### Production (Azure)

Deploy the server to Azure Container Apps or Azure App Service:

```bash
azd deploy
```

The infrastructure templates in `infra/` can be extended to deploy the M365 server alongside the DevUI.

## Configuration

The server uses the same `.env` configuration as the console and DevUI:

```bash
# .env
AZURE_OPENAI_ENDPOINT=https://your-project.services.ai.azure.com/api/projects/your-project-id
AZURE_OPENAI_API_VERSION=2024-05-01-preview
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME=gpt-5-mini
AZURE_OPENAI_WEB_DEPLOYMENT_NAME=gpt-4.1-mini
BING_SEARCH_API_URL=https://your-bing-search.cognitiveservices.azure.com/v7.0/search
```

### Authentication

For production deployments, configure JWT authentication:

```python
# In m365_server.py
from microsoft_agents.hosting.core import AgentAuthConfiguration
from microsoft_agents.authentication.msal import MsalConnectionManager

auth_config = AgentAuthConfiguration(
    # Your app registration details
    tenant_id="your-tenant-id",
    client_id="your-client-id",
    client_secret="your-client-secret",
)

# Pass to start_server
start_server(agent_app, auth_config)
```

## Usage Examples

### Microsoft Teams

1. **Register Bot**: Create a Bot Framework registration in Azure Portal
2. **Configure Messaging Endpoint**: Set to `https://your-server.azurecontainerapps.io/api/messages`
3. **Create Teams App Manifest**: Define app metadata and capabilities
4. **Upload to Teams**: Install the app in your Teams environment

Example conversation:
```
User: Plan a 50-person tech conference in Seattle with $25k budget
Bot: 👋 Starting your event planning...
     
     🏢 Venue Specialist needs your input:
     I found 3 venues in Seattle. Which do you prefer?
     1. Convention Center ($3000/day)
     2. Tech Hub Event Space ($1500/day)
     3. Hotel Grand Ballroom ($2500/day)

User: Option 2

Bot: ✨ Event Plan Complete:
     
     **Seattle Tech Conference - 50 people**
     - Venue: Tech Hub Event Space ($1500)
     - Catering: $15/person buffet ($750)
     - A/V Equipment: $500
     - Weather: Clear skies, 72°F
     ...
```

### Web Chat

Use the Bot Framework Web Chat control:

```html
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.botframework.com/botframework-webchat/latest/webchat.js"></script>
</head>
<body>
    <div id="webchat" role="main"></div>
    <script>
        window.WebChat.renderWebChat({
            directLine: window.WebChat.createDirectLine({
                token: 'YOUR_DIRECT_LINE_TOKEN'
            }),
            userID: 'user-id',
            username: 'User'
        }, document.getElementById('webchat'));
    </script>
</body>
</html>
```

## Human-in-the-Loop

The server handles `RequestInfoEvent` from the workflow by:

1. **Storing Request**: Saves `HumanFeedbackRequest` in `TurnState.pending_requests`
2. **Sending Prompt**: Uses `context.send_activity()` to ask the user
3. **Waiting for Response**: Next user message is treated as the response
4. **Resuming Workflow**: Calls `workflow.send_responses_streaming()` with user's answer

Example:
```python
# Workflow requests input
event = RequestInfoEvent(
    request_id="venue_choice",
    data=HumanFeedbackRequest(
        prompt="Which venue do you prefer?",
        requesting_agent="venue"
    )
)

# Server sends to user
await context.send_activity("🏢 Venue Specialist needs your input:\n\nWhich venue do you prefer?")

# Server waits for next message
# User sends: "Option 2"

# Server resumes workflow
stream = workflow.send_responses_streaming({"venue_choice": "Option 2"})
```

## Differences from Console.py

| Feature | Console.py | M365 Server |
|---------|-----------|-------------|
| **Interface** | CLI (stdin/stdout) | HTTP (Bot Framework Activities) |
| **Interaction** | Synchronous prompts | Asynchronous turn-based |
| **State** | In-memory during session | Persisted across turns (TurnState) |
| **Streaming** | Real-time display | Collected and sent on completion |
| **Human-in-the-loop** | Blocking input() | Non-blocking activity exchange |
| **Deployment** | Local only | Cloud-ready (Teams, Copilot, etc.) |

## Troubleshooting

### Port Already in Use

```bash
# Change port via environment variable
PORT=4000 uv run m365-server
```

### Authentication Errors

Ensure your `.env` has valid Azure credentials:
```bash
# Test authentication
az login
az account show
```

### Workflow Errors

Check the server logs for detailed error messages:
```python
# Errors are logged to stderr and sent to user
@agent_app.error
async def on_error(context: TurnContext, error: Exception):
    print(f"Error: {error}", file=sys.stderr)
    await context.send_activity("❌ An error occurred...")
```

## Development

### Testing Locally

Use the Bot Framework Emulator to test locally:

1. Download: https://github.com/Microsoft/BotFramework-Emulator
2. Start server: `uv run m365-server`
3. Connect to: `http://localhost:3978/api/messages`

### Adding Custom Activity Handlers

```python
@agent_app.message("/status")
async def on_status(context: TurnContext, state: WorkflowTurnState):
    """Handle custom /status command."""
    await context.send_activity(f"Workflow complete: {state.is_workflow_complete}")
```

### Extending TurnState

```python
@dataclass
class CustomTurnState(WorkflowTurnState):
    """Add custom fields to track additional state."""
    user_preferences: dict[str, Any] = field(default_factory=dict)
    conversation_history: list[str] = field(default_factory=list)
```

## References

- [Microsoft 365 Agents SDK Documentation](https://aka.ms/agents)
- [Microsoft Agents for Python GitHub](https://github.com/Microsoft/Agents-for-python)
- [Python Samples Repository](https://github.com/microsoft/Agents/tree/main/samples/python)
- [Bot Framework Activity Schema](https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-connector-activities)
