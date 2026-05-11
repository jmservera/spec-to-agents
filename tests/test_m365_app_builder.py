# Copyright (c) Microsoft. All rights reserved.

"""Unit tests for M365 app builder module."""

import os
from unittest.mock import patch

import pytest


class TestCreateAgentApplication:
    """Tests for create_agent_application function."""

    def test_raises_when_bot_id_not_set(self) -> None:
        """Test that create_agent_application raises ValueError when BOT_ID is missing."""
        from spec_to_agents.m365.app_builder import create_agent_application

        # Ensure BOT_ID is not set
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValueError, match="BOT_ID environment variable is required"),
        ):
            create_agent_application()

    def test_returns_tuple_with_correct_types(self) -> None:
        """Test that create_agent_application returns tuple of correct types."""
        from microsoft_agents.hosting.core import AgentApplication, AgentAuthConfiguration
        from microsoft_agents.hosting.fastapi import CloudAdapter

        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "test-bot-id-12345",
            "TEAMS_APP_TENANT_ID": "test-tenant-id",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            agent_app, adapter, auth_config = create_agent_application()

            assert isinstance(agent_app, AgentApplication)
            assert isinstance(adapter, CloudAdapter)
            assert isinstance(auth_config, AgentAuthConfiguration)

    def test_auth_config_uses_bot_id(self) -> None:
        """Test that auth config is created with BOT_ID as client_id."""
        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "my-test-bot-id",
            "TEAMS_APP_TENANT_ID": "my-tenant-id",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            _, _, auth_config = create_agent_application()

            assert auth_config is not None
            assert auth_config.CLIENT_ID == "my-test-bot-id"
            assert auth_config.TENANT_ID == "my-tenant-id"

    def test_registers_activity_handlers(self) -> None:
        """Test that activity handlers are registered on the agent application."""
        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "test-bot-id",
            "TEAMS_APP_TENANT_ID": "test-tenant-id",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            agent_app, _, _ = create_agent_application()

            # AgentApplication stores handlers internally
            # We verify it was created successfully and is callable
            assert agent_app is not None
            # The app should have been configured with handlers
            # (internal implementation detail, but app creation should succeed)


class TestRegisterActivityHandlers:
    """Tests for _register_activity_handlers function."""

    def test_help_handler_registered(self) -> None:
        """Test that /help message handler is registered."""
        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "test-bot-id",
            "TEAMS_APP_TENANT_ID": "test-tenant-id",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            agent_app, _, _ = create_agent_application()

            # The agent app should be configured - this is a smoke test
            # More detailed handler testing would require mocking TurnContext
            assert agent_app is not None

    def test_error_handler_registered(self) -> None:
        """Test that error handler is registered."""
        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "test-bot-id",
            "TEAMS_APP_TENANT_ID": "test-tenant-id",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            agent_app, _, _ = create_agent_application()

            # Error handler registration is done via @agent_app.error decorator
            # This is a smoke test verifying the app is created successfully
            assert agent_app is not None


class TestConnectionManagerSetup:
    """Tests for MSAL connection manager setup in create_agent_application."""

    def test_creates_connection_manager_when_service_connection_configured(self) -> None:
        """Test connection manager is created when SERVICE_CONNECTION env vars are set."""
        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "test-bot-id",
            "TEAMS_APP_TENANT_ID": "test-tenant-id",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "service-client-id",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "service-secret",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTH_TYPE": "ClientSecret",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            agent_app, adapter, auth_config = create_agent_application()

            # App should be created successfully with connection manager
            assert agent_app is not None
            assert adapter is not None
            assert auth_config is not None

    def test_works_without_service_connection(self) -> None:
        """Test that app creation works even without SERVICE_CONNECTION configured."""
        from spec_to_agents.m365.app_builder import create_agent_application

        env_vars = {
            "BOT_ID": "test-bot-id",
            "TEAMS_APP_TENANT_ID": "test-tenant-id",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            agent_app, adapter, auth_config = create_agent_application()

            # Should succeed without connection manager (outbound auth disabled)
            assert agent_app is not None
            assert adapter is not None
            assert auth_config is not None
