# Upstream Bugs

Bugs found in dependencies, with the versions needed to reproduce them and the workaround
carried in this repository. Remove an entry once the upstream fix is released and the
workaround is dropped.

---

## fastmcp 4.0.5 — `host_origin_protection` is silently ignored on the SSE transport

- **Package**: `fastmcp` 4.0.5
- **Python**: 3.10+ (reproduced on 3.13, macOS 15 / Darwin 25.6.0)
- **Found**: 2026-09-19
- **Severity**: security — the documented protection appears enabled but is absent

### Symptom

`FastMCP.http_app(transport="sse", host_origin_protection=True)` does not install
`HostOriginGuardMiddleware`. The same call with `transport="http"` or
`transport="streamable-http"` installs it correctly. No warning or error is raised, so the
caller believes Host and Origin validation is active when it is not.

The setting is accepted all the way through `run()` and `run_http_async()`, which makes the
silence particularly misleading.

### Reproduction

```python
from fastmcp import FastMCP

mcp = FastMCP("demo")
for transport in ("http", "streamable-http", "sse"):
    app = mcp.http_app(transport=transport, host_origin_protection=True)
    installed = [m.cls.__name__ for m in app.user_middleware]
    print(transport, "HostOriginGuardMiddleware" in installed)
```

```
http             True
streamable-http  True
sse              False
```

### Impact here

The MCP server is served over SSE so that `claude mcp add --transport sse` keeps working.
Without the guard, a request carrying a hostile `Origin`, or a rebound `Host`, is accepted:
both were observed returning HTTP 202 against a live server. The tools behind that endpoint
transmit on a CAN bus.

### Workaround

`canopen_studio.mcp_server` builds the SSE application itself and installs
`canopen_studio.agent_security.LocalOnlyMiddleware` on it, then serves it with uvicorn,
instead of relying on `mcp.run(..., host_origin_protection=True)`. The middleware applies
the same Host and Origin rules the project uses for the A2A server.

### Status

Not yet reported upstream.
