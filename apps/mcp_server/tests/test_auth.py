"""Tests for API-token auth + AuthContext resolution."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.api_auth import authenticate_token, create_token
from apps.mcp_server.context import AuthContext, MCPAuthError, MCPWorkspaceError


@pytest.mark.django_db
def test_authenticate_token_round_trip(mcp_user):
    _, raw = create_token(user=mcp_user, name="x")
    result = authenticate_token(raw)
    assert result is not None
    auth_user, auth_token = result
    assert auth_user.id == mcp_user.id
    assert auth_token.name == "x"


@pytest.mark.django_db
def test_authenticate_rejects_garbage_and_empty():
    assert authenticate_token("") is None
    assert authenticate_token(None) is None
    assert authenticate_token("bbn_does_not_exist") is None
    assert authenticate_token("not_a_brightbean_token") is None


@pytest.mark.django_db
def test_authenticate_rejects_revoked(mcp_user):
    token, raw = create_token(user=mcp_user, name="y")
    token.revoked_at = timezone.now()
    token.save()
    assert authenticate_token(raw) is None


@pytest.mark.django_db
def test_authenticate_rejects_expired(mcp_user):
    from datetime import timedelta

    token, raw = create_token(user=mcp_user, name="z")
    token.expires_at = timezone.now() - timedelta(hours=1)
    token.save()
    assert authenticate_token(raw) is None


@pytest.mark.django_db
def test_auth_context_picks_up_last_workspace(mcp_user, mcp_workspace, mcp_token):
    _, raw = mcp_token
    ctx = AuthContext.from_token(raw)
    assert ctx.user.id == mcp_user.id
    assert ctx.current_workspace is not None
    assert ctx.current_workspace.id == mcp_workspace.id


@pytest.mark.django_db
def test_scoped_token_cannot_switch_workspace(mcp_user, mcp_workspace, mcp_org):
    """A workspace-scoped token must refuse select_workspace to another workspace."""
    from apps.members.models import WorkspaceMembership
    from apps.workspaces.models import Workspace

    other = Workspace.objects.create(organization=mcp_org, name="Other")
    WorkspaceMembership.objects.create(
        user=mcp_user, workspace=other, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )

    _, raw = create_token(user=mcp_user, name="scoped", scoped_workspace=mcp_workspace)
    ctx = AuthContext.from_token(raw)
    assert ctx.current_workspace.id == mcp_workspace.id
    with pytest.raises(MCPAuthError):
        ctx.select_workspace(other.id)


@pytest.mark.django_db
def test_require_permission_blocks_when_missing(mcp_user, mcp_workspace, mcp_token):
    """A 'viewer' role should not pass create_posts permission."""
    from apps.members.models import WorkspaceMembership

    WorkspaceMembership.objects.filter(user=mcp_user, workspace=mcp_workspace).update(
        workspace_role=WorkspaceMembership.WorkspaceRole.VIEWER
    )

    _, raw = mcp_token
    ctx = AuthContext.from_token(raw)
    with pytest.raises(MCPAuthError):
        ctx.require_permission("create_posts")


@pytest.mark.django_db
def test_require_workspace_when_none_selected(mcp_user):
    _, raw = create_token(user=mcp_user, name="no-ws")
    ctx = AuthContext.from_token(raw)
    ctx.current_workspace = None
    with pytest.raises(MCPWorkspaceError):
        ctx.require_workspace()
