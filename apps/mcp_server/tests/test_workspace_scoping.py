"""Workspaces from one org must not be visible to a user who only belongs to another."""

from __future__ import annotations

import pytest

from apps.composer.services import create_post
from apps.mcp_server.context import AuthContext
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.mark.django_db
def test_list_posts_only_returns_current_workspace_posts(mcp_user, mcp_workspace, mcp_social_account, mcp_token):
    # Set up a SECOND org+workspace with its own owner — these posts must not leak.
    from django.utils import timezone

    from apps.accounts.models import User

    other_user = User.objects.create_user(email="other@example.com", password="x" * 12, tos_accepted_at=timezone.now())
    other_org = Organization.objects.create(name="Other Org")
    OrgMembership.objects.create(user=other_user, organization=other_org, org_role=OrgMembership.OrgRole.OWNER)
    other_ws = Workspace.objects.create(organization=other_org, name="Other WS")
    WorkspaceMembership.objects.create(
        user=other_user, workspace=other_ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )
    other_acct = SocialAccount.objects.create(
        workspace=other_ws,
        platform="facebook",
        account_platform_id="fb-other",
        account_name="Other FB",
        connection_status="connected",
    )

    # One post in each workspace.
    create_post(
        workspace=mcp_workspace,
        author=mcp_user,
        caption="mine",
        social_account_ids=[str(mcp_social_account.id)],
    )
    create_post(
        workspace=other_ws,
        author=other_user,
        caption="not mine",
        social_account_ids=[str(other_acct.id)],
    )

    # When the mcp_user lists posts, only their workspace's post shows up.
    from apps.composer.models import Post

    ctx = AuthContext.from_token(mcp_token[1])
    posts = list(Post.objects.for_workspace(ctx.current_workspace.id))
    assert len(posts) == 1
    assert posts[0].caption == "mine"


@pytest.mark.django_db
def test_select_workspace_rejects_non_member(mcp_user, mcp_workspace, mcp_token):
    other_org = Organization.objects.create(name="Foreign Org")
    foreign_ws = Workspace.objects.create(organization=other_org, name="Foreign WS")
    ctx = AuthContext.from_token(mcp_token[1])
    from apps.mcp_server.context import MCPAuthError

    with pytest.raises(MCPAuthError):
        ctx.select_workspace(foreign_ws.id)
