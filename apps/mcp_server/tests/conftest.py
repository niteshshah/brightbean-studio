"""Shared pytest fixtures for mcp_server tests."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.api_auth import create_token
from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture
def mcp_user(db):
    return User.objects.create_user(
        email="mcp-owner@example.com",
        password="x" * 12,
        name="MCP Owner",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def mcp_org(db, mcp_user):
    org = Organization.objects.create(name="MCP Org")
    OrgMembership.objects.create(user=mcp_user, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    return org


@pytest.fixture
def mcp_workspace(db, mcp_user, mcp_org):
    ws = Workspace.objects.create(organization=mcp_org, name="MCP Workspace")
    WorkspaceMembership.objects.create(
        user=mcp_user,
        workspace=ws,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )
    mcp_user.last_workspace_id = ws.id
    mcp_user.save(update_fields=["last_workspace_id"])
    return ws


@pytest.fixture
def mcp_social_account(db, mcp_workspace):
    return SocialAccount.objects.create(
        workspace=mcp_workspace,
        platform="facebook",
        account_platform_id="fb-test-1",
        account_name="Test FB Page",
        connection_status="connected",
    )


@pytest.fixture
def mcp_token(db, mcp_user, mcp_workspace):
    """Returns (ApiToken, raw_token_string)."""
    return create_token(user=mcp_user, name="test-token")
