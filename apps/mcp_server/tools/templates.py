"""list_templates, create_template_from_post, delete_template, create_post_from_template."""

from __future__ import annotations

from typing import Any

from apps.composer import services as composer_services
from apps.composer.models import Post, PostTemplate


def _tpl(t: PostTemplate) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "description": t.description,
        "created_at": t.created_at.isoformat(),
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_templates() -> list[dict[str, Any]]:
        """List post templates available in the current workspace."""
        ws = ctx.require_workspace()
        return [_tpl(t) for t in PostTemplate.objects.for_workspace(ws.id)]

    @mcp.tool()
    def create_template_from_post(post_id: str, name: str, description: str = "") -> dict[str, Any]:
        """Snapshot a Post's caption/first_comment/tags/category/media into a reusable template."""
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        post = (
            Post.objects.for_workspace(ws.id)
            .filter(pk=post_id)
            .prefetch_related("media_attachments", "platform_posts")
            .first()
        )
        if post is None:
            raise ValueError(f"Post {post_id} not found in current workspace.")
        media_ids = [str(m.media_asset_id) for m in post.media_attachments.order_by("position")]
        platform_ids = [str(p.social_account_id) for p in post.platform_posts.all()]
        template_data = {
            "caption": post.caption,
            "first_comment": post.first_comment,
            "title": post.title,
            "category_id": str(post.category_id) if post.category_id else None,
            "tags": list(post.tags or []),
            "media_asset_ids": media_ids,
            "social_account_ids": platform_ids,
        }
        tpl = PostTemplate.objects.create(
            workspace=ws,
            name=name,
            description=description,
            template_data=template_data,
            created_by=ctx.user,
        )
        return _tpl(tpl)

    @mcp.tool()
    def delete_template(template_id: str) -> dict[str, Any]:
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        tpl = PostTemplate.objects.for_workspace(ws.id).filter(pk=template_id).first()
        if tpl is None:
            raise ValueError(f"Template {template_id} not found.")
        tpl.delete()
        return {"deleted": True, "id": template_id}

    @mcp.tool()
    def create_post_from_template(
        template_id: str,
        social_account_ids: list[str] | None = None,
        scheduled_at: str | None = None,
    ) -> dict[str, Any]:
        """Instantiate a draft Post from a template. Overrides accounts/schedule if provided."""
        from datetime import datetime

        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        tpl = PostTemplate.objects.for_workspace(ws.id).filter(pk=template_id).first()
        if tpl is None:
            raise ValueError(f"Template {template_id} not found.")
        data = tpl.template_data or {}
        accounts = social_account_ids or data.get("social_account_ids") or []
        if not accounts:
            raise ValueError("Template has no social_account_ids — pass social_account_ids explicitly.")

        sched = datetime.fromisoformat(scheduled_at) if scheduled_at else None
        from apps.composer.models import PlatformPost

        post = composer_services.create_post(
            workspace=ws,
            author=ctx.user,
            caption=data.get("caption", ""),
            title=data.get("title", ""),
            first_comment=data.get("first_comment", ""),
            social_account_ids=accounts,
            tags=data.get("tags") or [],
            media_asset_ids=data.get("media_asset_ids") or [],
            scheduled_at=sched,
            initial_status=PlatformPost.Status.SCHEDULED if sched else PlatformPost.Status.DRAFT,
        )
        return {"post_id": str(post.id), "template_id": str(tpl.id)}
