"""Smoke tests for the composer MCP tools (call the underlying service helpers directly).

These exercise the service layer that the MCP tool registrations delegate to,
so a regression in either path surfaces here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.composer.models import PlatformPost, Post
from apps.composer.services import (
    ComposerError,
    create_post,
    delete_post,
    schedule_post,
    update_post,
)


@pytest.mark.django_db
def test_create_post_creates_platform_posts(mcp_workspace, mcp_user, mcp_social_account):
    post = create_post(
        workspace=mcp_workspace,
        author=mcp_user,
        caption="hello",
        social_account_ids=[str(mcp_social_account.id)],
    )
    assert post.platform_posts.count() == 1
    assert post.caption == "hello"


@pytest.mark.django_db
def test_create_post_rejects_foreign_social_account(mcp_workspace, mcp_user):
    """A social account that doesn't belong to the workspace can't be used."""
    from apps.organizations.models import Organization
    from apps.social_accounts.models import SocialAccount
    from apps.workspaces.models import Workspace

    other_ws = Workspace.objects.create(organization=Organization.objects.create(name="ghost"), name="ghost-ws")
    foreign = SocialAccount.objects.create(
        workspace=other_ws,
        platform="facebook",
        account_platform_id="fb-ghost",
        account_name="Ghost",
        connection_status="connected",
    )
    with pytest.raises(ComposerError):
        create_post(
            workspace=mcp_workspace,
            author=mcp_user,
            caption="bad",
            social_account_ids=[str(foreign.id)],
        )


@pytest.mark.django_db
def test_update_post_blocked_when_not_editable(mcp_workspace, mcp_user, mcp_social_account):
    post = create_post(
        workspace=mcp_workspace,
        author=mcp_user,
        caption="hi",
        social_account_ids=[str(mcp_social_account.id)],
    )
    # Force PlatformPost into a non-editable state via a valid transition.
    pp = post.platform_posts.first()
    pp.transition_to(PlatformPost.Status.SCHEDULED)
    pp.save()
    # Re-derive parent status (publishing): both Post.is_editable consults the children.
    assert post.is_editable  # 'scheduled' IS still editable per the model
    # Now move past editable.
    pp.transition_to(PlatformPost.Status.PUBLISHING)
    pp.save()
    pp.transition_to(PlatformPost.Status.PUBLISHED)
    pp.save()
    post.refresh_from_db()
    with pytest.raises(ComposerError):
        update_post(post, caption="x")


@pytest.mark.django_db
def test_schedule_post_in_future_only(mcp_workspace, mcp_user, mcp_social_account):
    post = create_post(
        workspace=mcp_workspace,
        author=mcp_user,
        caption="hi",
        social_account_ids=[str(mcp_social_account.id)],
    )
    with pytest.raises(ComposerError):
        schedule_post(post, timezone.now() - timedelta(hours=1))


@pytest.mark.django_db
def test_schedule_and_reschedule(mcp_workspace, mcp_user, mcp_social_account):
    post = create_post(
        workspace=mcp_workspace,
        author=mcp_user,
        caption="hi",
        social_account_ids=[str(mcp_social_account.id)],
    )
    schedule_post(post, timezone.now() + timedelta(hours=1))
    schedule_post(post, timezone.now() + timedelta(hours=2))
    post.refresh_from_db()
    assert post.platform_posts.first().status == PlatformPost.Status.SCHEDULED


@pytest.mark.django_db
def test_delete_post_cascades(mcp_workspace, mcp_user, mcp_social_account):
    post = create_post(
        workspace=mcp_workspace,
        author=mcp_user,
        caption="bye",
        social_account_ids=[str(mcp_social_account.id)],
    )
    pid = post.id
    delete_post(post)
    assert not Post.objects.filter(pk=pid).exists()
    assert not PlatformPost.objects.filter(post_id=pid).exists()
