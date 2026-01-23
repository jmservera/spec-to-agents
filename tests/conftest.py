# Copyright (c) Microsoft. All rights reserved.

"""Shared pytest fixtures for test suite."""

from contextlib import suppress
from unittest.mock import Mock

import pytest

from spec_to_agents.container import AppContainer


def pytest_addoption(parser):
    """Add custom pytest command line options."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow integration tests (full workflow tests)",
    )


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (run with --run-slow)")


def pytest_collection_modifyitems(config, items):
    """Skip slow tests unless --run-slow is provided."""
    if config.getoption("--run-slow"):
        # --run-slow given: do not skip slow tests
        return

    skip_slow = pytest.mark.skip(reason="Need --run-slow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(autouse=True)
def setup_di_container():
    """
    Set up and wire DI container for all tests.

    This fixture automatically runs before each test to ensure the DI container
    is properly configured. It mocks out the client and global_tools providers
    to avoid making real API calls during tests.

    The fixture is autouse=True, so it runs automatically for all tests without
    needing to be explicitly requested.
    """
    # Create container
    container = AppContainer()

    # Override providers with mocks for testing
    container.client.override(Mock())
    container.global_tools.override({})
    container.model_config.override({})

    # Wire the container to enable @inject decorators
    container.wire(
        packages=[
            "spec_to_agents.agents",
            "spec_to_agents.workflow",
        ]
    )

    # Yield control to the test
    yield container

    # Cleanup: unwire after test
    with suppress(Exception):
        # Ignore unwiring errors during cleanup
        container.unwire()
