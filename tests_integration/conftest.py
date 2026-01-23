# Copyright (c) Microsoft. All rights reserved.

from contextlib import suppress
from pathlib import Path

import pytest
from dotenv import load_dotenv

from spec_to_agents.container import AppContainer


@pytest.fixture
def load_test_env():
    """Load test-specific environment variables."""
    env_path = Path(__file__).parent.parent / ".env.test"
    load_dotenv(env_path, override=True)
    yield


@pytest.fixture
def load_integration_env():
    """Load integration test environment."""
    env_path = Path(__file__).parent.parent / "env/.env"
    load_dotenv(f"{env_path}.local", override=True)
    load_dotenv(f"{env_path}.local.user", override=True)
    yield


@pytest.fixture
def setup_di_container():
    """
    Set up and wire DI container for integration tests with REAL providers.

    Unlike unit tests, integration tests need real Azure connections.
    This fixture wires the container WITHOUT mocking the client,
    allowing actual API calls to Azure AI services.
    """
    from spec_to_agents.workflow.core import get_shared_agents

    # Clear any cached agents from previous tests
    get_shared_agents.cache_clear()

    # Create container (uses real providers from config)
    container = AppContainer()

    # Wire the container to enable @inject decorators
    # NOTE: Do NOT override client/global_tools - use real providers
    container.wire(
        packages=[
            "spec_to_agents.agents",
            "spec_to_agents.workflow",
        ]
    )

    # Yield control to the test
    yield container

    # Cleanup: unwire after test and clear cached agents
    with suppress(Exception):
        container.unwire()

    # Clear cached agents to ensure fresh state for next test
    get_shared_agents.cache_clear()
