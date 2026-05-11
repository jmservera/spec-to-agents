# Copyright (c) Microsoft. All rights reserved.

"""Unit tests for M365 routes module."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestCreateM365SubApp:
    """Tests for create_m365_subapp function."""

    def test_creates_fastapi_app(self) -> None:
        """Test that create_m365_subapp returns a FastAPI instance."""
        from spec_to_agents.m365.routes import create_m365_subapp

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        subapp = create_m365_subapp(mock_agent_app, mock_adapter)

        assert isinstance(subapp, FastAPI)
        assert subapp.title == "M365 Agents API"

    def test_has_messages_post_endpoint(self) -> None:
        """Test that subapp has POST /messages endpoint."""
        from spec_to_agents.m365.routes import create_m365_subapp

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        subapp = create_m365_subapp(mock_agent_app, mock_adapter)

        # Find POST /messages route
        post_messages_routes = [
            r
            for r in subapp.routes
            if getattr(r, "path", "") == "/messages" and "POST" in getattr(r, "methods", set())  # pyright: ignore[reportUnknownArgumentType]
        ]
        assert len(post_messages_routes) == 1

    def test_has_messages_get_endpoint(self) -> None:
        """Test that subapp has GET /messages health check endpoint."""
        from spec_to_agents.m365.routes import create_m365_subapp

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        subapp = create_m365_subapp(mock_agent_app, mock_adapter)

        # Find GET /messages route
        get_messages_routes = [
            r
            for r in subapp.routes
            if getattr(r, "path", "") == "/messages" and "GET" in getattr(r, "methods", set())  # pyright: ignore[reportUnknownArgumentType]
        ]
        assert len(get_messages_routes) == 1

    def test_get_messages_returns_healthy(self) -> None:
        """Test that GET /messages returns health status."""
        from spec_to_agents.m365.routes import create_m365_subapp

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        subapp = create_m365_subapp(mock_agent_app, mock_adapter)
        client = TestClient(subapp)

        response = client.get("/messages")

        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "endpoint": "/api/messages"}

    def test_adds_jwt_middleware_when_auth_config_provided(self) -> None:
        """Test that JWT middleware is added when auth config is provided."""
        from spec_to_agents.m365.routes import create_m365_subapp

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()
        mock_auth_config = MagicMock()

        subapp = create_m365_subapp(mock_agent_app, mock_adapter, mock_auth_config)

        # Verify auth config is set on app state
        assert subapp.state.agent_configuration is mock_auth_config

    def test_no_jwt_middleware_when_no_auth_config(self) -> None:
        """Test that no JWT middleware is added when auth config is None."""
        from spec_to_agents.m365.routes import create_m365_subapp

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        subapp = create_m365_subapp(mock_agent_app, mock_adapter, auth_configuration=None)

        # Verify no auth config on state
        assert not hasattr(subapp.state, "agent_configuration")


class TestInitializeM365Components:
    """Tests for initialize_m365_components function."""

    def test_mounts_subapp_at_api(self) -> None:
        """Test that M365 subapp is mounted at /api."""
        from spec_to_agents.m365.routes import initialize_m365_components

        parent_app = FastAPI()
        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        initialize_m365_components(parent_app, mock_agent_app, mock_adapter)

        # Find /api mount
        api_mounts = [r for r in parent_app.routes if getattr(r, "path", "") == "/api"]
        assert len(api_mounts) == 1

    def test_api_mount_is_first_in_route_list(self) -> None:
        """
        Test that /api mount is moved to front of route list.

        This is critical to avoid DevUI's catch-all mount intercepting /api requests.
        """
        from spec_to_agents.m365.routes import initialize_m365_components

        parent_app = FastAPI()

        # Add some existing routes
        @parent_app.get("/existing")
        async def existing():  # type: ignore
            return {"status": "ok"}

        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        initialize_m365_components(parent_app, mock_agent_app, mock_adapter)

        # First route should be /api mount
        first_route = parent_app.routes[0]
        assert getattr(first_route, "path", "") == "/api"

    def test_messages_endpoint_accessible_via_api_path(self) -> None:
        """Test that /api/messages endpoint is accessible on parent app."""
        from spec_to_agents.m365.routes import initialize_m365_components

        parent_app = FastAPI()
        mock_agent_app = MagicMock()
        mock_adapter = MagicMock()

        initialize_m365_components(parent_app, mock_agent_app, mock_adapter)

        client = TestClient(parent_app)
        response = client.get("/api/messages")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
