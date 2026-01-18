# Copyright (c) Microsoft. All rights reserved.

import json

from agent_framework import (
    AgentRunUpdateEvent,
    FunctionCallContent,
    FunctionResultContent,
    TextContent,
)

from spec_to_agents.models.messages import HumanFeedbackRequest


def display_human_feedback_request(
    feedback_request: HumanFeedbackRequest,
) -> str:
    """
    Display a human feedback request and prompt for user response.

    Parameters
    ----------
    feedback_request : HumanFeedbackRequest
        The feedback request containing prompt, context, and metadata

    Returns
    -------
    str
        The formatted human feedback request
    """
    # Determine agent color for styling
    agent_name = feedback_request.requesting_agent.upper()
    agent_icon = AGENT_ICONS.get(agent_name.lower(), "🤖")

    # Build context display
    context_display = ""
    if feedback_request.context:
        context_lines = ["*Additional Context:*"]
        for key, value in feedback_request.context.items():
            if isinstance(value, list):
                context_lines.append(f"**{key}:**")
                for item in value:  # type: ignore
                    context_lines.append(f"  • {item}")
            elif isinstance(value, dict):
                context_lines.append(f"**{key}:**")
                for k, v in value.items():  # type: ignore
                    context_lines.append(f"  {k}: {v}")
            else:
                context_lines.append(f"**{key}:** {value}")
        context_display = "\n" + "\n".join(context_lines)

    # Display styled request panel
    return (
        f"# {agent_icon} {agent_name} Agent Request 🤔\n\n"
        f"**Type:** {feedback_request.request_type}\n\n"
        f"**Question:**\n{feedback_request.prompt}"
        f"{context_display}"
    )


def display_final_output(workflow_output: str) -> str:
    """
    Display the final workflow output with intelligent formatting.

    Attempts to parse as JSON and render with appropriate styling:
    - JSON with 'summary' field: Markdown summary + JSON details
    - Pure JSON: Syntax-highlighted JSON
    - Markdown text: Rendered markdown
    - Fallback: Plain text in styled panel

    Parameters
    ----------
    workflow_output : str
        The final output from the workflow
    """
    final_output = ["# Final Event Plan"]

    # Parse and display the workflow output
    try:
        # Try to parse as JSON first
        output_data = json.loads(workflow_output)

        # Extract summary if it exists and is markdown
        if isinstance(output_data, dict) and "summary" in output_data and isinstance(output_data["summary"], str):
            summary_content = str(output_data["summary"])

            # Render markdown summary
            final_output.append("## Event Plan Summary")
            final_output.append(summary_content)

            # Display other fields if present
            other_fields = {k: v for k, v in output_data.items() if k != "summary"}  # type: ignore
            if other_fields:
                final_output.append("\n## Additional Details")
                final_output.append("```json\n" + json.dumps(other_fields, indent=2, ensure_ascii=False) + "\n```")
        else:
            # Display entire JSON output with syntax highlighting
            final_output.append("## Event Plan")
            final_output.append("```json\n" + json.dumps(output_data, indent=2, ensure_ascii=False) + "\n```")
    except json.JSONDecodeError:
        # If not JSON, try rendering as markdown
        final_output.append("## Event Plan")
        final_output.append(workflow_output)

    return "\n".join(final_output)


AGENT_ICONS = {
    "venue": "🏢",
    "budget": "💰",
    "catering": "🍽️",
    "logistics": "📅",
    "coordinator": "🎯",
}


def get_agent_icon(executor_id: str) -> str:
    """
    Get the icon associated with an agent type.

    Parameters
    ----------
    executor_id : str
        The executor/agent ID

    Returns
    -------
    str
        Rich color name for the agent
    """
    # Match executor_id to agent type
    for agent_type, icon in AGENT_ICONS.items():
        if agent_type in executor_id.lower():
            return icon

    return "🤖"


details_open = False


def close_agent_details() -> str:
    """
    Close any open agent details section.

    Returns
    -------
    str
        The closing details tag if open, else empty string
    """
    global details_open
    if details_open:
        details_open = False
        return "</details>"
    return ""


def display_agent_run_update(
    event: AgentRunUpdateEvent,
    last_executor: str | None,
    printed_tool_calls: set[str],
    printed_tool_results: set[str],
) -> tuple[str, str | None]:
    """
    Display an AgentRunUpdateEvent in a readable format using Rich styling.

    Streams agent execution updates including tool calls, tool results, and text output.
    Tracks which tool calls and results have been displayed to avoid duplication
    during streaming. Uses Rich library for color-coded, styled output.

    Parameters
    ----------
    event : AgentRunUpdateEvent
        The workflow event containing an agent run update
    last_executor : str | None
        The ID of the last executor that was displayed. Used to print executor
        transitions.
    printed_tool_calls : set[str]
        Set of call IDs that have already been printed. Modified in place.
    printed_tool_results : set[str]
        Set of call IDs for results that have already been printed. Modified in place.

    Returns
    -------
    str | None
        The executor_id if it changed, None otherwise. Use this to track executor
        transitions between calls.

    Notes
    -----
    This function prints directly to stdout using Rich Console and modifies the
    printed_tool_calls and printed_tool_results sets in place. The function handles
    three types of content updates:
    - FunctionCallContent: Displayed as styled panel with function name and arguments
    - FunctionResultContent: Displayed as styled panel with call ID and result
    - Text updates: Streamed with agent-specific color coding

    Examples
    --------
    >>> printed_calls = set()
    >>> printed_results = set()
    >>> last_exec = None
    >>> async for event in workflow.run_stream(prompt):
    ...     if isinstance(event, AgentRunUpdateEvent):
    ...         last_exec = display_agent_run_update(event, last_exec, printed_calls, printed_results)
    """
    global details_open

    final_output: list[str] = []
    executor_id = event.executor_id
    update = event.data

    # Safety check: ensure update has contents
    if update is None or update.contents is None:
        return "", last_executor

    # Extract function calls and results from the update
    function_calls = [c for c in update.contents if isinstance(c, FunctionCallContent)]
    function_results = [c for c in update.contents if isinstance(c, FunctionResultContent)]

    # Get agent-specific color
    agent_icon = get_agent_icon(executor_id)

    # Print executor ID when it changes
    if executor_id != last_executor:
        if last_executor is not None:
            final_output.append("</details>")  # Close previous executor details

        final_output.append(f"<details>\n<summary>📌 {agent_icon} <b>{executor_id}</b></summary>")
        details_open = True
        # Display agent header with color-coded styling
        last_executor = executor_id

    # Print any new tool calls before the text update
    for call in function_calls:
        if call.call_id in printed_tool_calls:
            continue
        printed_tool_calls.add(call.call_id)

        # Format arguments for display
        args = call.arguments
        if isinstance(args, dict):
            args_display = "```json\n" + json.dumps(args, indent=2, ensure_ascii=False) + "\n```"
        else:
            args_str = (args or "").strip()
            args_display = args_str

        # Build panel content with header and arguments as separate renderables
        header = f"**🔧 Tool Call:** `{call.name}`\n**Call ID:** *{call.call_id}*\n"

        final_output.append("## Function Call")
        final_output.append(header)
        final_output.append(args_display)

    # Print any new tool results before the text update
    for result in function_results:
        if result.call_id in printed_tool_results:
            continue
        printed_tool_results.add(result.call_id)

        # Format result for display
        result_text = result.result
        # Handle different result types safely
        if isinstance(result_text, str):
            # String result - use directly
            if len(result_text) > 500:
                result_text = result_text[:500] + f"... *(truncated, {len(result_text)} chars total)*"
            result_display = result_text
        elif isinstance(result_text, list):
            # List result - could be list of TextContent objects or other types
            # Try to extract text from TextContent objects if present
            extracted_texts: list[str] = []
            for item in result_text:  # type: ignore[union-attr]
                if isinstance(item, TextContent):
                    # Extract text attribute from TextContent objects
                    extracted_texts.append(item.text)
                elif isinstance(item, str):
                    extracted_texts.append(item)
                else:
                    # For other types, convert to string
                    extracted_texts.append(f"{item}")

            # Join all extracted texts
            combined_text = "\n".join(extracted_texts)
            if len(combined_text) > 500:
                combined_text = combined_text[:500] + f"... *(truncated, {len(combined_text)} chars total)*"
            result_display = combined_text
        elif isinstance(result_text, dict):
            # JSON-serializable dict - format as JSON
            try:
                result_display = "```json\n" + json.dumps(result_text, indent=2, ensure_ascii=False) + "\n```"
            except (TypeError, ValueError):
                # Fallback if json.dumps fails
                result_display = "*Error*: Unable to serialize result to JSON."
        elif result_text is None:
            # None result
            result_display = "*None*"
        elif isinstance(result_text, TextContent):
            # Single TextContent object - extract text directly
            result_str = result_text.text
            if len(result_str) > 500:
                result_str = result_str[:500] + f"... *(truncated, {len(result_str)} chars total)*"
            result_display = result_str
        else:
            # Complex object (Pydantic models, etc.) - convert to string
            result_str = str(result_text)
            if len(result_str) > 500:
                result_str = result_str[:500] + f"... *(truncated, {len(result_str)} chars total)*"
            result_display = result_str

        # Build panel content with proper Rich renderable handling
        call_id_text = f"### Call ID: *{result.call_id}*"
        # For string results, use f-string as normal
        result_panel_content = f"{call_id_text}\n\n{result_display}"

        final_output.append("### Function Result")
        final_output.append(result_panel_content)

    # Finally, print the text update with agent-specific color
    if update.text is not None:
        final_output.append(update.text)
    return "\n".join(final_output), last_executor
