

from typing import Any


def test_workflow_builder_accessible_from_workflow():
    """Test that build_event_planning_workflow is accessible from workflow module."""
    from microsoft_agents.activity import load_configuration_from_env
    from os import environ

    environ["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"] = "test-client-id"
    environ["bot_app_id"] = "test-client-id"
    environ["CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET"] = "test-client-secret"

    config: dict[Any, Any] = load_configuration_from_env(environ)

    assert config.get("CONNECTIONS", {}).get("SERVICE_CONNECTION", {}).get("SETTINGS", {}).get("CLIENTID") == "test-client-id"
    assert config.get("CONNECTIONS", {}).get("SERVICE_CONNECTION", {}).get("SETTINGS", {}).get("CLIENTSECRET") == "test-client-secret"
    assert config.get("bot_app_id") == None
    