"""list_inbox, get_inbox_message, send_reply, add_internal_note, change_status, assign_message."""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from apps.inbox import services as inbox_services
from apps.inbox.models import InboxMessage, InboxSLAConfig, SavedReply
from apps.members.models import WorkspaceMembership


def _serialize_message(msg: InboxMessage) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "social_account_id": str(msg.social_account_id),
        "platform": msg.social_account.platform if msg.social_account_id else None,
        "message_type": msg.message_type,
        "status": msg.status,
        "sentiment": msg.sentiment,
        "sender_name": msg.sender_name,
        "sender_handle": msg.sender_handle,
        "body": msg.body,
        "assigned_to_id": str(msg.assigned_to_id) if msg.assigned_to_id else None,
        "parent_message_id": str(msg.parent_message_id) if msg.parent_message_id else None,
        "related_post_id": str(msg.related_post_id) if msg.related_post_id else None,
        "received_at": msg.received_at.isoformat() if msg.received_at else None,
        "created_at": msg.created_at.isoformat(),
    }


def _serialize_message_full(msg: InboxMessage) -> dict[str, Any]:
    data = _serialize_message(msg)
    data["thread"] = []
    for reply in msg.replies.select_related("author").order_by("sent_at"):
        data["thread"].append(
            {
                "kind": "reply",
                "id": str(reply.id),
                "body": reply.body,
                "author_email": reply.author.email if reply.author_id else None,
                "platform_reply_id": reply.platform_reply_id,
                "sent_at": reply.sent_at.isoformat() if reply.sent_at else None,
            }
        )
    for note in msg.internal_notes.select_related("author").order_by("created_at"):
        data["thread"].append(
            {
                "kind": "note",
                "id": str(note.id),
                "body": note.body,
                "author_email": note.author.email if note.author_id else None,
                "created_at": note.created_at.isoformat(),
            }
        )
    data["thread"].sort(key=lambda x: x.get("sent_at") or x.get("created_at") or "")
    return data


def _get_message(ctx, message_id: str) -> InboxMessage:
    ws = ctx.require_workspace()
    msg = (
        InboxMessage.objects.filter(pk=message_id, workspace_id=ws.id)
        .select_related("social_account", "assigned_to")
        .first()
    )
    if msg is None:
        raise ValueError(f"InboxMessage {message_id} not found in current workspace.")
    return msg


def register(mcp, ctx):
    @mcp.tool()
    def list_inbox(
        status: str | None = None,
        sentiment: str | None = None,
        message_type: str | None = None,
        social_account_id: str | None = None,
        assigned_to_me: bool = False,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List inbox messages in the current workspace.

        Filters: status (unread|open|resolved|archived), sentiment (positive|neutral|negative),
        message_type (comment|mention|dm|review), social_account_id, assigned_to_me.
        """
        ctx.require_permission("use_inbox")
        ws = ctx.require_workspace()
        qs = InboxMessage.objects.filter(workspace_id=ws.id).select_related("social_account", "assigned_to")
        if status:
            qs = qs.filter(status=status)
        if sentiment:
            qs = qs.filter(sentiment=sentiment)
        if message_type:
            qs = qs.filter(message_type=message_type)
        if social_account_id:
            qs = qs.filter(social_account_id=social_account_id)
        if assigned_to_me:
            qs = qs.filter(assigned_to=ctx.user)
        if search:
            qs = qs.filter(Q(body__icontains=search) | Q(sender_name__icontains=search))
        qs = qs.order_by("-received_at", "-created_at")
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        total = qs.count()
        items = [_serialize_message(m) for m in qs[offset : offset + limit]]
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    @mcp.tool()
    def get_inbox_message(message_id: str) -> dict[str, Any]:
        """Return one inbox message with its full reply + internal-note thread."""
        ctx.require_permission("use_inbox")
        msg = _get_message(ctx, message_id)
        return _serialize_message_full(msg)

    @mcp.tool()
    def send_reply(message_id: str, body: str) -> dict[str, Any]:
        """Send a reply to an inbox message via the underlying platform."""
        ctx.require_permission("reply_from_inbox")
        msg = _get_message(ctx, message_id)
        reply = inbox_services.send_reply(message=msg, body=body, author=ctx.user)
        return {
            "id": str(reply.id),
            "message_id": str(msg.id),
            "platform_reply_id": reply.platform_reply_id,
            "message_status": msg.status,
        }

    @mcp.tool()
    def add_internal_note(message_id: str, body: str) -> dict[str, Any]:
        """Attach an internal note (team-only) to an inbox message."""
        ctx.require_permission("reply_from_inbox")
        msg = _get_message(ctx, message_id)
        note = inbox_services.add_internal_note(message=msg, body=body, author=ctx.user)
        return {"id": str(note.id), "message_id": str(msg.id)}

    @mcp.tool()
    def change_status(message_id: str, status: str) -> dict[str, Any]:
        """Set inbox message status: unread | open | resolved | archived."""
        ctx.require_permission("reply_from_inbox")
        valid = {c[0] for c in InboxMessage.Status.choices}
        if status not in valid:
            raise ValueError(f"Invalid status '{status}'. Valid: {sorted(valid)}")
        msg = _get_message(ctx, message_id)
        inbox_services.change_status(message=msg, status=status)
        return _serialize_message(msg)

    @mcp.tool()
    def assign_message(message_id: str, user_id: str | None = None) -> dict[str, Any]:
        """Assign an inbox message to a workspace member (or unassign with user_id=None)."""
        ctx.require_permission("reply_from_inbox")
        msg = _get_message(ctx, message_id)
        ws = ctx.require_workspace()
        assignee = None
        if user_id:
            membership = (
                WorkspaceMembership.objects.filter(workspace=ws, user_id=user_id).select_related("user").first()
            )
            if membership is None:
                raise ValueError(f"User {user_id} is not a member of this workspace.")
            assignee = membership.user
        inbox_services.assign_message(message=msg, assignee=assignee, actor=ctx.user)
        return _serialize_message(msg)

    @mcp.tool()
    def bulk_inbox_action(
        message_ids: list[str],
        action: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Bulk update inbox messages.

        action: mark_read | resolve | archive | assign
        For action='assign', pass user_id (or null to unassign).
        """
        ctx.require_permission("reply_from_inbox")
        ws = ctx.require_workspace()
        assignee = None
        if action == "assign" and user_id:
            membership = WorkspaceMembership.objects.filter(workspace=ws, user_id=user_id).first()
            if membership is None:
                raise ValueError(f"User {user_id} is not a workspace member.")
            assignee = membership.user
        count = inbox_services.bulk_action(workspace=ws, message_ids=message_ids, action=action, assignee=assignee)
        return {"action": action, "updated": count}

    @mcp.tool()
    def list_saved_replies() -> list[dict[str, Any]]:
        """List saved-reply templates in the current workspace."""
        ctx.require_permission("use_inbox")
        ws = ctx.require_workspace()
        return [{"id": str(r.id), "title": r.title, "body": r.body} for r in SavedReply.objects.for_workspace(ws.id)]

    @mcp.tool()
    def create_saved_reply(title: str, body: str) -> dict[str, Any]:
        ctx.require_permission("reply_from_inbox")
        ws = ctx.require_workspace()
        r = SavedReply.objects.create(workspace=ws, title=title, body=body, created_by=ctx.user)
        return {"id": str(r.id), "title": r.title, "body": r.body}

    @mcp.tool()
    def delete_saved_reply(saved_reply_id: str) -> dict[str, Any]:
        ctx.require_permission("reply_from_inbox")
        ws = ctx.require_workspace()
        r = SavedReply.objects.for_workspace(ws.id).filter(pk=saved_reply_id).first()
        if r is None:
            raise ValueError(f"SavedReply {saved_reply_id} not found.")
        r.delete()
        return {"deleted": True, "id": saved_reply_id}

    @mcp.tool()
    def render_saved_reply(saved_reply_id: str, message_id: str) -> dict[str, Any]:
        """Render a saved reply against a message's context (sender_name, account_name, post_url)."""
        ctx.require_permission("use_inbox")
        ws = ctx.require_workspace()
        r = SavedReply.objects.for_workspace(ws.id).filter(pk=saved_reply_id).first()
        if r is None:
            raise ValueError(f"SavedReply {saved_reply_id} not found.")
        msg = _get_message(ctx, message_id)
        body = r.render(
            {
                "sender_name": msg.sender_name,
                "account_name": msg.social_account.account_name if msg.social_account_id else "",
                "post_url": "",
            }
        )
        return {"body": body, "saved_reply_id": str(r.id)}

    @mcp.tool()
    def get_inbox_sla() -> dict[str, Any]:
        """Return the SLA config (target response minutes, auto-resolve-on-reply) for this workspace."""
        ctx.require_permission("use_inbox")
        ws = ctx.require_workspace()
        cfg = InboxSLAConfig.objects.filter(workspace=ws).first()
        if cfg is None:
            return {"is_active": False, "target_response_minutes": 0, "auto_resolve_on_reply": False}
        return {
            "is_active": cfg.is_active,
            "target_response_minutes": cfg.target_response_minutes,
            "auto_resolve_on_reply": cfg.auto_resolve_on_reply,
        }

    @mcp.tool()
    def update_inbox_sla(
        target_response_minutes: int | None = None,
        is_active: bool | None = None,
        auto_resolve_on_reply: bool | None = None,
    ) -> dict[str, Any]:
        """Update SLA config (per-workspace singleton)."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        cfg, _ = InboxSLAConfig.objects.get_or_create(workspace=ws)
        fields = []
        if target_response_minutes is not None:
            cfg.target_response_minutes = target_response_minutes
            fields.append("target_response_minutes")
        if is_active is not None:
            cfg.is_active = is_active
            fields.append("is_active")
        if auto_resolve_on_reply is not None:
            cfg.auto_resolve_on_reply = auto_resolve_on_reply
            fields.append("auto_resolve_on_reply")
        if fields:
            cfg.save(update_fields=fields)
        return {
            "is_active": cfg.is_active,
            "target_response_minutes": cfg.target_response_minutes,
            "auto_resolve_on_reply": cfg.auto_resolve_on_reply,
        }
