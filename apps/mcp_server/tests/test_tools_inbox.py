"""Smoke tests for the inbox service helpers."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.inbox.models import InboxMessage
from apps.inbox.services import (
    add_internal_note,
    assign_message,
    bulk_action,
    change_status,
)


@pytest.fixture
def inbox_message(db, mcp_workspace, mcp_social_account):
    return InboxMessage.objects.create(
        workspace=mcp_workspace,
        social_account=mcp_social_account,
        platform_message_id="m-1",
        message_type="comment",
        sender_name="Tester",
        body="hello",
        status=InboxMessage.Status.UNREAD,
        received_at=timezone.now(),
    )


@pytest.mark.django_db
def test_change_status(inbox_message):
    change_status(message=inbox_message, status="resolved")
    inbox_message.refresh_from_db()
    assert inbox_message.status == "resolved"


@pytest.mark.django_db
def test_add_internal_note(inbox_message, mcp_user):
    note = add_internal_note(message=inbox_message, body="acknowledged", author=mcp_user)
    assert note.body == "acknowledged"
    assert note.inbox_message_id == inbox_message.id


@pytest.mark.django_db
def test_assign_message(inbox_message, mcp_user):
    assign_message(message=inbox_message, assignee=mcp_user, actor=mcp_user)
    inbox_message.refresh_from_db()
    assert inbox_message.assigned_to_id == mcp_user.id


@pytest.mark.django_db
def test_bulk_action_resolve(inbox_message, mcp_workspace):
    count = bulk_action(workspace=mcp_workspace, message_ids=[inbox_message.id], action="resolve")
    assert count == 1
    inbox_message.refresh_from_db()
    assert inbox_message.status == "resolved"


@pytest.mark.django_db
def test_bulk_action_unknown_raises(mcp_workspace):
    with pytest.raises(ValueError):
        bulk_action(workspace=mcp_workspace, message_ids=[], action="vaporize")
