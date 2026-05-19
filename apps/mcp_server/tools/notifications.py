"""list_notifications, mark_notification_read, mark_all_read,
get_notification_preferences, update_notification_preferences."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.notifications.models import Notification, NotificationPreference


def _serialize(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "event_type": n.event_type,
        "title": n.title,
        "body": n.body,
        "data": n.data,
        "is_read": n.is_read,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat(),
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_notifications(unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        """List notifications for the authenticated user."""
        qs = Notification.objects.filter(user=ctx.user).order_by("-created_at")
        if unread_only:
            qs = qs.filter(is_read=False)
        limit = max(1, min(int(limit), 200))
        return [_serialize(n) for n in qs[:limit]]

    @mcp.tool()
    def mark_notification_read(notification_id: str) -> dict[str, Any]:
        n = Notification.objects.filter(user=ctx.user, pk=notification_id).first()
        if n is None:
            raise ValueError(f"Notification {notification_id} not found.")
        if not n.is_read:
            n.is_read = True
            n.read_at = timezone.now()
            n.save(update_fields=["is_read", "read_at"])
        return _serialize(n)

    @mcp.tool()
    def mark_all_read() -> dict[str, Any]:
        """Mark every notification for the current user as read."""
        count = Notification.objects.filter(user=ctx.user, is_read=False).update(is_read=True, read_at=timezone.now())
        return {"updated": count}

    @mcp.tool()
    def get_notification_preferences() -> list[dict[str, Any]]:
        """List notification preferences for the authenticated user."""
        prefs = NotificationPreference.objects.filter(user=ctx.user)
        return [
            {
                "event_type": p.event_type,
                "channel": p.channel,
                "is_enabled": p.is_enabled,
            }
            for p in prefs
        ]

    @mcp.tool()
    def update_notification_preferences(
        preferences: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Bulk upsert preferences. Each item: {event_type, channel, is_enabled}."""
        updated = 0
        for p in preferences:
            NotificationPreference.objects.update_or_create(
                user=ctx.user,
                event_type=p["event_type"],
                channel=p["channel"],
                defaults={"is_enabled": bool(p["is_enabled"])},
            )
            updated += 1
        return {"updated": updated}
