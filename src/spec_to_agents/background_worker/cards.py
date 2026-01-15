# Copyright (c) Microsoft. All rights reserved.

"""Adaptive Cards for displaying job progress and results."""

from typing import Any


def progress_card(job_id: str, percent: int, summary: str) -> dict[str, Any]:
    """
    Create an Adaptive Card showing job progress with refresh capability.

    Parameters
    ----------
    job_id : str
        The job ID for tracking
    percent : int
        Progress percentage (0-100)
    summary : str
        Human-readable progress summary

    Returns
    -------
    dict[str, Any]
        Adaptive Card JSON schema
    """
    return {
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "size": "Medium",
                "weight": "Bolder",
                "text": "Working on your request",
            },
            {"type": "TextBlock", "isSubtle": True, "wrap": True, "text": summary},
            {
                "type": "TextBlock",
                "text": f"Progress: {percent}%",
                "wrap": True,
            },
            {"type": "ProgressBar", "value": percent / 100.0},
        ],
        "actions": [
            {
                "type": "Action.Execute",
                "title": "Continue",
                "verb": "continue",
                "data": {"jobId": job_id},
            },
            {
                "type": "Action.Execute",
                "title": "Cancel",
                "verb": "cancel",
                "style": "destructive",
                "data": {"jobId": job_id},
            },
        ],
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "refresh": {
            "userIds": ["*"],
            "action": {
                "type": "Action.Execute",
                "title": "Refresh",
                "verb": "refresh",
                "data": {"jobId": job_id},
            },
        },
    }


def final_card(status: str, summary: str, download_url: str | None = None) -> dict[str, Any]:
    """
    Create an Adaptive Card showing job completion.

    Parameters
    ----------
    status : str
        Final job status ('succeeded', 'failed', 'cancelled')
    summary : str
        Human-readable summary of the result
    download_url : str | None, optional
        URL for downloading results (if applicable)

    Returns
    -------
    dict[str, Any]
        Adaptive Card JSON schema
    """
    title = "Task complete" if status == "succeeded" else "Task finished"
    body = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "text": title,
        },
        {"type": "TextBlock", "wrap": True, "text": summary},
    ]

    actions: list[dict[str, Any]] = []
    if download_url:
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "Open Result",
                "url": download_url,
            }
        )

    return {
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body,
        "actions": actions,
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
    }
