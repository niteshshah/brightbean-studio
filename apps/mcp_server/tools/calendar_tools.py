"""Calendar tools: queues, posting slots, custom events, reschedule.

(Named ``calendar_tools`` to avoid clashing with Python's stdlib ``calendar``.)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from apps.calendar import services as calendar_services
from apps.calendar.models import CustomCalendarEvent, PostingSlot, Queue, QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.publisher import services as publisher_services


def _queue(q: Queue) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "name": q.name,
        "category_id": str(q.category_id) if q.category_id else None,
        "social_account_id": str(q.social_account_id) if q.social_account_id else None,
        "is_active": q.is_active,
    }


def _entry(e: QueueEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "queue_id": str(e.queue_id),
        "post_id": str(e.post_id),
        "position": e.position,
        "assigned_slot_datetime": e.assigned_slot_datetime.isoformat() if e.assigned_slot_datetime else None,
    }


def _slot(s: PostingSlot) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "social_account_id": str(s.social_account_id),
        "day_of_week": s.day_of_week,
        "time": s.time.isoformat(timespec="minutes"),
        "is_active": s.is_active,
    }


def _event(ev: CustomCalendarEvent) -> dict[str, Any]:
    return {
        "id": str(ev.id),
        "title": ev.title,
        "description": ev.description,
        "start_date": ev.start_date.isoformat(),
        "end_date": ev.end_date.isoformat(),
        "color": ev.color,
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_queues() -> list[dict[str, Any]]:
        """List posting queues in the current workspace."""
        ws = ctx.require_workspace()
        return [_queue(q) for q in Queue.objects.for_workspace(ws.id)]

    @mcp.tool()
    def get_queue(queue_id: str) -> dict[str, Any]:
        """Return a queue with all its entries in position order."""
        ws = ctx.require_workspace()
        q = Queue.objects.for_workspace(ws.id).filter(pk=queue_id).first()
        if q is None:
            raise ValueError(f"Queue {queue_id} not found.")
        return {**_queue(q), "entries": [_entry(e) for e in q.entries.order_by("position")]}

    @mcp.tool()
    def add_to_queue(post_id: str, queue_id: str, priority: bool = False) -> dict[str, Any]:
        """Append (or insert priority) a Post into a queue. Slots are assigned by the queue runner."""
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        post = Post.objects.for_workspace(ws.id).filter(pk=post_id).first()
        q = Queue.objects.for_workspace(ws.id).filter(pk=queue_id).first()
        if post is None or q is None:
            raise ValueError("Post or queue not found in current workspace.")
        entry = calendar_services.add_to_queue(post, q, priority=priority)
        return _entry(entry)

    @mcp.tool()
    def reorder_queue(queue_id: str, ordered_entry_ids: list[str]) -> dict[str, Any]:
        """Rewrite the position of every entry in a queue."""
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        q = Queue.objects.for_workspace(ws.id).filter(pk=queue_id).first()
        if q is None:
            raise ValueError(f"Queue {queue_id} not found.")
        calendar_services.reorder_queue(q, ordered_entry_ids)
        return {"queue_id": queue_id, "count": len(ordered_entry_ids)}

    @mcp.tool()
    def list_posting_slots(social_account_id: str) -> list[dict[str, Any]]:
        """List posting-time slots configured for a social account."""
        ctx.require_workspace()
        slots = PostingSlot.objects.filter(social_account_id=social_account_id).order_by("day_of_week", "time")
        return [_slot(s) for s in slots]

    @mcp.tool()
    def upsert_posting_slots(social_account_id: str, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace all posting slots for an account.

        Each slot: {"day_of_week": 0..6, "time": "HH:MM", "is_active": true}.
        """
        ctx.require_permission("manage_social_accounts")
        ctx.require_workspace()
        PostingSlot.objects.filter(social_account_id=social_account_id).delete()
        from datetime import time as dt_time

        created = []
        for s in slots:
            hh, mm = s["time"].split(":")[:2]
            slot = PostingSlot.objects.create(
                social_account_id=social_account_id,
                day_of_week=int(s["day_of_week"]),
                time=dt_time(int(hh), int(mm)),
                is_active=bool(s.get("is_active", True)),
            )
            created.append(slot)
        return [_slot(s) for s in created]

    @mcp.tool()
    def reschedule_platform_post(platform_post_id: str, new_datetime: str) -> dict[str, Any]:
        """Move a single platform variant to a new scheduled datetime (ISO 8601)."""
        ws = ctx.require_workspace()
        pp = PlatformPost.objects.select_related("post").filter(pk=platform_post_id, post__workspace_id=ws.id).first()
        if pp is None:
            raise ValueError(f"PlatformPost {platform_post_id} not found in current workspace.")
        if pp.post.author_id == ctx.user.id:
            ctx.require_permission("edit_own_posts")
        else:
            ctx.require_permission("edit_others_posts")
        new_dt = datetime.fromisoformat(new_datetime)
        from django.utils import timezone as tz

        valid_from = {
            PlatformPost.Status.DRAFT,
            PlatformPost.Status.APPROVED,
            PlatformPost.Status.SCHEDULED,
            PlatformPost.Status.CHANGES_REQUESTED,
            PlatformPost.Status.FAILED,
        }
        if pp.status not in valid_from:
            raise ValueError(f"PlatformPost in status '{pp.status}' cannot be rescheduled.")
        if new_dt <= tz.now():
            raise ValueError("new_datetime must be in the future.")
        pp.scheduled_at = new_dt
        if pp.status != PlatformPost.Status.SCHEDULED:
            pp.transition_to(PlatformPost.Status.SCHEDULED)
        pp.save(update_fields=["scheduled_at", "status", "updated_at"])
        return {
            "id": str(pp.id),
            "status": pp.status,
            "scheduled_at": pp.scheduled_at.isoformat(),
        }

    @mcp.tool()
    def list_custom_events(from_date: str | None = None, to_date: str | None = None) -> list[dict[str, Any]]:
        """List custom calendar events. Optional date filter (ISO date)."""
        ws = ctx.require_workspace()
        qs = CustomCalendarEvent.objects.for_workspace(ws.id)
        if from_date:
            qs = qs.filter(end_date__gte=date.fromisoformat(from_date))
        if to_date:
            qs = qs.filter(start_date__lte=date.fromisoformat(to_date))
        return [_event(e) for e in qs]

    @mcp.tool()
    def create_custom_event(
        title: str,
        start_date: str,
        end_date: str,
        description: str = "",
        color: str = "",
    ) -> dict[str, Any]:
        """Create a custom calendar event (campaigns, launches, etc.)."""
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        ev = CustomCalendarEvent.objects.create(
            workspace=ws,
            title=title,
            description=description,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            color=color,
            created_by=ctx.user,
        )
        return _event(ev)

    @mcp.tool()
    def delete_custom_event(event_id: str) -> dict[str, Any]:
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        ev = CustomCalendarEvent.objects.for_workspace(ws.id).filter(pk=event_id).first()
        if ev is None:
            raise ValueError(f"Event {event_id} not found.")
        ev.delete()
        return {"deleted": True, "id": event_id}

    # silence "unused import" — publisher_services is exposed for potential future tools here
    _ = publisher_services
