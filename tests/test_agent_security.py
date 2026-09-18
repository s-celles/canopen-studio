"""
Unit tests for the access controls guarding the MCP and A2A servers.

Both servers transmit on a CAN bus, so a request reaching them can command real hardware.
"""

import pytest

from canopen_studio import agent_security as sec


class TestLocalHostHeader:
    """Rejecting a foreign Host header is what blocks DNS rebinding."""

    @pytest.mark.parametrize(
        "host",
        ["localhost", "localhost:3001", "127.0.0.1", "127.0.0.1:8765", "[::1]:3001", "::1"],
    )
    def test_loopback_hosts_are_accepted(self, host):
        assert sec.host_is_local(host) is True

    @pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "192.168.1.10:3001"])
    def test_foreign_hosts_are_rejected(self, host):
        assert sec.host_is_local(host) is False

    def test_a_missing_host_is_accepted(self):
        """HTTP/1.1 requires Host; a client omitting it is not a browser."""
        assert sec.host_is_local(None) is True


class TestOrigin:
    """A browser always sets Origin on a cross-origin POST, and cannot forge it."""

    def test_no_origin_is_accepted(self):
        """Command-line clients and MCP agents send no Origin at all."""
        assert sec.origin_is_allowed(None) is True

    def test_any_origin_is_rejected_by_default(self):
        assert sec.origin_is_allowed("https://evil.example") is False

    def test_localhost_origin_is_rejected_by_default(self):
        """Nothing legitimate reaches these servers from a page, not even a local one."""
        assert sec.origin_is_allowed("http://localhost:3001") is False

    def test_an_explicitly_allowed_origin_passes(self):
        assert sec.origin_is_allowed("https://studio.example", {"https://studio.example"}) is True

    def test_allowlist_does_not_admit_others(self):
        assert sec.origin_is_allowed("https://evil.example", {"https://studio.example"}) is False


class TestRequestGuard:
    """The two checks combined, as the servers apply them."""

    def test_a_plain_client_request_passes(self):
        assert sec.rejection_reason(host="localhost:8765", origin=None) is None

    def test_a_browser_request_is_named_in_the_rejection(self):
        reason = sec.rejection_reason(host="localhost:8765", origin="https://evil.example")
        assert reason is not None and "origin" in reason.lower()

    def test_a_rebound_host_is_named_in_the_rejection(self):
        reason = sec.rejection_reason(host="evil.example", origin=None)
        assert reason is not None and "host" in reason.lower()


class TestEnvFlag:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values(self, value, monkeypatch):
        monkeypatch.setenv("CANOPEN_TEST_FLAG", value)
        assert sec.env_flag("CANOPEN_TEST_FLAG", default=False) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_values(self, value, monkeypatch):
        monkeypatch.setenv("CANOPEN_TEST_FLAG", value)
        assert sec.env_flag("CANOPEN_TEST_FLAG", default=True) is False

    def test_default_is_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("CANOPEN_TEST_FLAG", raising=False)
        assert sec.env_flag("CANOPEN_TEST_FLAG", default=True) is True
        assert sec.env_flag("CANOPEN_TEST_FLAG", default=False) is False


class TestServerEnablement:
    """A2A is opt-in because it is trivially reachable from a web page; MCP is not."""

    def test_a2a_is_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("CANOPEN_STUDIO_A2A", raising=False)
        from canopen_studio import a2a_server

        assert a2a_server.is_enabled() is False

    def test_a2a_can_be_enabled(self, monkeypatch):
        monkeypatch.setenv("CANOPEN_STUDIO_A2A", "1")
        from canopen_studio import a2a_server

        assert a2a_server.is_enabled() is True

    def test_mcp_is_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("CANOPEN_STUDIO_MCP", raising=False)
        from canopen_studio import mcp_server

        assert mcp_server.is_enabled() is True

    def test_mcp_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("CANOPEN_STUDIO_MCP", "0")
        from canopen_studio import mcp_server

        assert mcp_server.is_enabled() is False


class TestLocalOnlyMiddleware:
    """
    The ASGI guard actually mounted on both servers.

    It exists because fastmcp 4.0.5 silently ignores host_origin_protection on the SSE
    transport, so the project cannot rely on the built-in one.
    """

    @pytest.fixture
    def client(self):
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        async def ok(request):
            return PlainTextResponse("reached the bus")

        app = Starlette(routes=[Route("/", ok, methods=["GET", "POST"])])
        app.add_middleware(sec.LocalOnlyMiddleware)
        # The default base_url sends Host: testserver, which the guard rightly refuses.
        return TestClient(app, base_url="http://localhost:8765")

    def test_a_plain_client_reaches_the_application(self, client):
        assert client.post("/").status_code == 200

    def test_a_browser_origin_is_refused(self, client):
        response = client.post("/", headers={"Origin": "https://evil.example"})

        assert response.status_code == 403
        assert "reached the bus" not in response.text

    def test_a_localhost_origin_is_refused(self, client):
        assert client.post("/", headers={"Origin": "http://localhost:3001"}).status_code == 403

    def test_a_rebound_host_is_refused(self, client):
        assert client.post("/", headers={"Host": "evil.example"}).status_code == 403

    def test_an_allowlisted_origin_passes(self, client, monkeypatch):
        monkeypatch.setenv("CANOPEN_STUDIO_ALLOWED_ORIGINS", "https://studio.example")

        assert client.post("/", headers={"Origin": "https://studio.example"}).status_code == 200

    def test_get_requests_are_guarded_too(self, client):
        """The SSE stream is a GET, and it hands out the session id."""
        assert client.get("/", headers={"Origin": "https://evil.example"}).status_code == 403
