"""Idea kanban: list_idea_groups, create_idea_group, list_ideas, create_idea,
update_idea, move_idea, delete_idea, convert_idea_to_post."""

from __future__ import annotations

from typing import Any

from apps.composer import services as composer_services
from apps.composer.models import Idea, IdeaGroup, IdeaMedia


def _group(g: IdeaGroup) -> dict[str, Any]:
    return {"id": str(g.id), "name": g.name, "position": g.position}


def _idea(i: Idea) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "title": i.title,
        "description": i.description,
        "tags": i.tags or [],
        "status": i.status,
        "group_id": str(i.group_id) if i.group_id else None,
        "position": i.position,
        "post_id": str(i.post_id) if i.post_id else None,
        "media_ids": [str(m.media_asset_id) for m in i.media_attachments.order_by("position")],
        "created_at": i.created_at.isoformat(),
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_idea_groups() -> list[dict[str, Any]]:
        """List Kanban groups in the current workspace."""
        ws = ctx.require_workspace()
        return [_group(g) for g in IdeaGroup.objects.for_workspace(ws.id)]

    @mcp.tool()
    def create_idea_group(name: str) -> dict[str, Any]:
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        position = (IdeaGroup.objects.for_workspace(ws.id).count() or 0) + 1
        g = IdeaGroup.objects.create(workspace=ws, name=name, position=position)
        return _group(g)

    @mcp.tool()
    def list_ideas(group_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        """List ideas. Optional filters: group_id, status (unassigned|todo|in_progress|done)."""
        ws = ctx.require_workspace()
        qs = Idea.objects.for_workspace(ws.id).prefetch_related("media_attachments")
        if group_id:
            qs = qs.filter(group_id=group_id)
        if status:
            qs = qs.filter(status=status)
        return [_idea(i) for i in qs]

    @mcp.tool()
    def create_idea(
        title: str,
        description: str = "",
        group_id: str | None = None,
        tags: list[str] | None = None,
        media_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        idea = Idea.objects.create(
            workspace=ws,
            author=ctx.user,
            title=title,
            description=description,
            group_id=group_id or None,
            tags=list(tags or []),
        )
        if media_asset_ids:
            IdeaMedia.objects.bulk_create(
                [IdeaMedia(idea=idea, media_asset_id=mid, position=idx) for idx, mid in enumerate(media_asset_ids)]
            )
        return _idea(idea)

    @mcp.tool()
    def update_idea(
        idea_id: str,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        idea = Idea.objects.for_workspace(ws.id).filter(pk=idea_id).first()
        if idea is None:
            raise ValueError(f"Idea {idea_id} not found.")
        fields = []
        if title is not None:
            idea.title = title
            fields.append("title")
        if description is not None:
            idea.description = description
            fields.append("description")
        if tags is not None:
            idea.tags = list(tags)
            fields.append("tags")
        if group_id is not None:
            idea.group_id = group_id or None
            fields.append("group")
        if fields:
            fields.append("updated_at")
            idea.save(update_fields=fields)
        return _idea(idea)

    @mcp.tool()
    def move_idea(idea_id: str, group_id: str | None, position: int) -> dict[str, Any]:
        """Move an idea to a group at the given position."""
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        idea = Idea.objects.for_workspace(ws.id).filter(pk=idea_id).first()
        if idea is None:
            raise ValueError(f"Idea {idea_id} not found.")
        idea.group_id = group_id or None
        idea.position = position
        idea.save(update_fields=["group", "position", "updated_at"])
        return _idea(idea)

    @mcp.tool()
    def delete_idea(idea_id: str) -> dict[str, Any]:
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        idea = Idea.objects.for_workspace(ws.id).filter(pk=idea_id).first()
        if idea is None:
            raise ValueError(f"Idea {idea_id} not found.")
        idea.delete()
        return {"deleted": True, "id": idea_id}

    @mcp.tool()
    def convert_idea_to_post(idea_id: str, social_account_ids: list[str]) -> dict[str, Any]:
        """Promote an Idea to a draft Post; carries title, description (as caption), tags, media."""
        ctx.require_permission("create_posts")
        ws = ctx.require_workspace()
        idea = Idea.objects.for_workspace(ws.id).filter(pk=idea_id).prefetch_related("media_attachments").first()
        if idea is None:
            raise ValueError(f"Idea {idea_id} not found.")
        if idea.post_id:
            raise ValueError(f"Idea {idea_id} has already been converted to a post.")

        media_ids = [str(m.media_asset_id) for m in idea.media_attachments.order_by("position")]
        post = composer_services.create_post(
            workspace=ws,
            author=ctx.user,
            caption=idea.description,
            title=idea.title,
            social_account_ids=social_account_ids,
            tags=list(idea.tags or []),
            media_asset_ids=media_ids,
        )
        idea.post = post
        idea.status = Idea.Status.DONE
        idea.save(update_fields=["post", "status", "updated_at"])
        return {"idea_id": str(idea.id), "post_id": str(post.id)}
