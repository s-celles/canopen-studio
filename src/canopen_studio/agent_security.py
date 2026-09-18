"""
Access controls for the MCP and A2A servers.

These servers transmit on a CAN bus, so a request that reaches one of them can command
real hardware. They listen on the loopback interface, which keeps remote machines out but
does not keep a *web page* out: a browser can POST to localhost from any site the user
visits. Two header checks close that path.

Origin
    A browser always attaches Origin to a cross-origin POST and a page cannot forge it.
    Command-line clients and MCP agents send none at all. Rejecting any request that
    carries an Origin therefore blocks pages without affecting legitimate clients.

Host
    A DNS rebinding attack resolves an attacker-controlled name to 127.0.0.1, after which
    the page is same-origin with the server and Origin may be absent. The Host header
    still names the attacker's domain, so requiring a loopback Host closes that variant.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Iterable, Optional, Set

# Host header values that denote this machine's loopback interface.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment variable, falling back to the given default when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _strip_port(host: str) -> str:
    """Drop the port from a Host header, keeping bracketed IPv6 literals intact."""
    if host.startswith("["):
        closing = host.find("]")
        return host[: closing + 1] if closing != -1 else host
    # More than one colon means a bare IPv6 literal, which carries no port to strip.
    if host.count(":") > 1:
        return host
    return host.rsplit(":", 1)[0] if ":" in host else host


def host_is_local(host: Optional[str]) -> bool:
    """
    Check that a Host header names this machine's loopback interface.

    A missing Host is accepted: HTTP/1.1 requires it, so a client omitting it is not a
    browser and is not the threat this guards against.
    """
    if not host:
        return True
    return _strip_port(host.strip().lower()) in LOCAL_HOSTS


def origin_is_allowed(origin: Optional[str], allowed: Optional[Iterable[str]] = None) -> bool:
    """
    Check an Origin header against the allowlist.

    With no allowlist, any Origin is refused: nothing legitimate reaches these servers
    from a web page, not even one served from localhost.
    """
    if not origin:
        return True
    permitted: Set[str] = {o.strip().rstrip("/").lower() for o in (allowed or ()) if o.strip()}
    return origin.strip().rstrip("/").lower() in permitted


def allowed_origins_from_env(name: str = "CANOPEN_STUDIO_ALLOWED_ORIGINS") -> Set[str]:
    """Read a comma-separated origin allowlist, for the rare front-end that needs one."""
    raw = os.environ.get(name, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def rejection_reason(host: Optional[str], origin: Optional[str]) -> Optional[str]:
    """
    Return why a request must be refused, or None when it may proceed.

    The message is meant for a log line and an HTTP error body, so it says which header
    was at fault without echoing its value back to the caller.
    """
    if not host_is_local(host):
        return "Refused: the Host header does not name the loopback interface."
    if not origin_is_allowed(origin, allowed_origins_from_env()):
        return "Refused: requests carrying an Origin header are not accepted."
    return None


class LocalOnlyMiddleware:
    """
    Pure-ASGI guard applying the Host and Origin rules above to every request.

    Written as raw ASGI rather than a framework middleware so the same class can wrap the
    FastAPI application of the A2A server and the Starlette application fastmcp builds for
    the MCP server.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = rejection_reason(host=headers.get("host"), origin=headers.get("origin"))
        if reason is None:
            await self.app(scope, receive, send)
            return

        body = reason.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
