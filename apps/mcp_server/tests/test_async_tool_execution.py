"""Regression tests: FastMCP runs tool callbacks inside an asyncio event loop,
so any handler that touches the Django ORM directly raises
`SynchronousOnlyOperation`. `build_server` installs a `sync_to_async` shim that
makes sync tool bodies safe; these tests pin that behavior.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.mcp_server.context import AuthContext
from apps.mcp_server.server import build_server


def _call(server, name, arguments=None):
    return asyncio.run(server._tool_manager.call_tool(name, arguments or {}))


@pytest.mark.django_db(transaction=True)
def test_list_workspaces_callable_from_async_context(mcp_user, mcp_workspace, mcp_token):
    """`list_workspaces` does ORM work; it must survive being awaited on a running loop."""
    pytest.importorskip("mcp.server.fastmcp")

    _, raw = mcp_token
    ctx = AuthContext.from_token(raw)
    server = build_server(ctx)

    result = _call(server, "list_workspaces")
    assert isinstance(result, list)
    assert any(w["id"] == str(mcp_workspace.id) for w in result)


@pytest.mark.django_db(transaction=True)
def test_whoami_callable_from_async_context(mcp_user, mcp_workspace, mcp_token):
    pytest.importorskip("mcp.server.fastmcp")

    _, raw = mcp_token
    ctx = AuthContext.from_token(raw)
    server = build_server(ctx)

    result = _call(server, "whoami")
    assert result["user"]["id"] == str(mcp_user.id)
    assert result["current_workspace"]["id"] == str(mcp_workspace.id)
