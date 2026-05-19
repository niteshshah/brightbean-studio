"""Analytics aggregates: publish metrics, publish logs, rate-limit state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db.models import Count

from apps.composer.models import PlatformPost
from apps.publisher.models import PublishLog, RateLimitState
from apps.social_accounts.models import SocialAccount


def register(mcp, ctx):
    @mcp.tool()
    def get_publish_metrics(
        from_datetime: str | None = None,
        to_datetime: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate publish counts by platform + status for the current workspace.

        Defaults to all-time. Datetimes are ISO 8601.
        """
        ctx.require_permission("view_analytics")
        ws = ctx.require_workspace()
        qs = PlatformPost.objects.filter(post__workspace_id=ws.id)
        if from_datetime:
            qs = qs.filter(published_at__gte=datetime.fromisoformat(from_datetime))
        if to_datetime:
            qs = qs.filter(published_at__lte=datetime.fromisoformat(to_datetime))

        by_status = list(qs.values("status").annotate(count=Count("id")).order_by("status"))
        by_platform = list(
            qs.values("social_account__platform").annotate(count=Count("id")).order_by("social_account__platform")
        )
        return {
            "total": qs.count(),
            "by_status": [{"status": r["status"], "count": r["count"]} for r in by_status],
            "by_platform": [{"platform": r["social_account__platform"], "count": r["count"]} for r in by_platform],
        }

    @mcp.tool()
    def list_publish_logs(
        post_id: str | None = None,
        platform_post_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent publish log entries for the current workspace."""
        ctx.require_permission("view_analytics")
        ws = ctx.require_workspace()
        qs = PublishLog.objects.filter(platform_post__post__workspace_id=ws.id).order_by("-created_at")
        if post_id:
            qs = qs.filter(platform_post__post_id=post_id)
        if platform_post_id:
            qs = qs.filter(platform_post_id=platform_post_id)
        limit = max(1, min(int(limit), 200))
        return [
            {
                "id": str(log.id),
                "platform_post_id": str(log.platform_post_id),
                "attempt_number": log.attempt_number,
                "status_code": log.status_code,
                "error_message": log.error_message,
                "duration_ms": log.duration_ms,
                "created_at": log.created_at.isoformat(),
            }
            for log in qs[:limit]
        ]

    @mcp.tool()
    def get_rate_limit_status(social_account_id: str) -> dict[str, Any]:
        """Return the most recently observed rate-limit window for an account."""
        ws = ctx.require_workspace()
        if not SocialAccount.objects.for_workspace(ws.id).filter(pk=social_account_id).exists():
            raise ValueError(f"SocialAccount {social_account_id} not found.")
        state = RateLimitState.objects.filter(social_account_id=social_account_id).first()
        if state is None:
            return {"social_account_id": social_account_id, "state": None}
        return {
            "social_account_id": social_account_id,
            "platform": state.platform,
            "requests_remaining": state.requests_remaining,
            "window_resets_at": state.window_resets_at.isoformat() if state.window_resets_at else None,
            "last_updated": state.last_updated.isoformat(),
        }
