# Copyright (c) Microsoft. All rights reserved.
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from agent_framework.azure import AzureAIAgentClient
from azure.identity.aio import AzureCliCredential, ChainedTokenCredential, ManagedIdentityCredential


def _is_container_environment() -> bool:
    """Check if running in a container environment (Azure Container Apps, etc.)."""
    return os.getenv("CONTAINER_ENV") == "true"


def _get_managed_identity_credential() -> ManagedIdentityCredential:
    """
    Create a ManagedIdentityCredential with the correct client ID for user-assigned identities.

    Returns
    -------
    ManagedIdentityCredential
        Credential configured with user-assigned managed identity client ID if available.
    """
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return ManagedIdentityCredential()


def _create_credential_chain() -> ChainedTokenCredential:
    """
    Create a ChainedTokenCredential with appropriate order based on environment.

    In container environments, ManagedIdentityCredential is tried first.
    In local development, AzureCliCredential is tried first to avoid IMDS timeout.
    """
    if _is_container_environment():
        # In Azure: try managed identity first, fall back to CLI
        return ChainedTokenCredential(
            _get_managed_identity_credential(),
            AzureCliCredential(),
        )
    # Local dev: try CLI first to avoid IMDS timeout noise
    return ChainedTokenCredential(
        AzureCliCredential(),
        _get_managed_identity_credential(),
    )


def create_agent_client_for_devui() -> AzureAIAgentClient:
    """
    Create an AzureAIAgentClient for DevUI lazy loading (non-context-managed).

    Returns
    -------
    AzureAIAgentClient
        A client instance for DevUI. DevUI handles cleanup via FastAPI lifespan hooks.

    Warnings
    --------
    This function creates a client WITHOUT automatic cleanup. Only use this for
    DevUI lazy loading where FastAPI's lifespan management handles cleanup.
    For all other use cases, use create_agent_client() as an async context manager.

    Notes
    -----
    The credential's HTTP session will remain open until explicitly closed by
    the application's shutdown hooks. This is intentional for DevUI integration.
    """
    return AzureAIAgentClient(async_credential=_create_credential_chain())


@asynccontextmanager
async def create_agent_client() -> AsyncIterator[AzureAIAgentClient]:
    """
    Create a new AzureAIAgentClient with proper resource cleanup.

    Yields
    ------
    AzureAIAgentClient
        A client instance for creating and managing Azure AI agents.
        The client and its underlying credential are automatically cleaned up
        when the context exits.

    Notes
    -----
    This async context manager ensures proper cleanup of both the agent client
    and the Azure credential's HTTP session. Always use as a context manager:

        async with create_agent_client() as client:
            agent = client.create_agent(...)
            # Agent and credential automatically cleaned up on exit

    The credential's aiohttp session will be properly closed, preventing
    "Unclosed client session" warnings.

    Examples
    --------
    >>> async with create_agent_client() as client:
    ...     agent = client.create_agent(
    ...         name="MyAgent",
    ...         instructions="...",
    ...         tools=[...],
    ...     )
    ...     result = agent.run("Hello")
    """
    credential = _create_credential_chain()
    client = AzureAIAgentClient(async_credential=credential)

    try:
        async with client:
            yield client
    finally:
        # Ensure credential's HTTP session is closed
        await credential.close()
