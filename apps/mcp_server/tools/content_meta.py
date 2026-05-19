"""list_categories, create_category, update_category, delete_category, list_tags, create_tag."""

from __future__ import annotations

from typing import Any

from apps.composer.models import ContentCategory, Tag


def _cat(c: ContentCategory) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "color": c.color,
        "position": c.position,
    }


def _tag(t: Tag) -> dict[str, Any]:
    return {"id": str(t.id), "name": t.name}


def register(mcp, ctx):
    @mcp.tool()
    def list_categories() -> list[dict[str, Any]]:
        """List content categories in the current workspace."""
        ws = ctx.require_workspace()
        return [_cat(c) for c in ContentCategory.objects.for_workspace(ws.id)]

    @mcp.tool()
    def create_category(name: str, color: str = "") -> dict[str, Any]:
        """Create a new content category."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        c = ContentCategory.objects.create(workspace=ws, name=name, color=color)
        return _cat(c)

    @mcp.tool()
    def update_category(category_id: str, name: str | None = None, color: str | None = None) -> dict[str, Any]:
        """Rename or recolor a category."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        c = ContentCategory.objects.for_workspace(ws.id).filter(pk=category_id).first()
        if c is None:
            raise ValueError(f"Category {category_id} not found.")
        fields = []
        if name is not None:
            c.name = name
            fields.append("name")
        if color is not None:
            c.color = color
            fields.append("color")
        if fields:
            fields.append("updated_at")
            c.save(update_fields=fields)
        return _cat(c)

    @mcp.tool()
    def delete_category(category_id: str) -> dict[str, Any]:
        """Delete a category."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        c = ContentCategory.objects.for_workspace(ws.id).filter(pk=category_id).first()
        if c is None:
            raise ValueError(f"Category {category_id} not found.")
        c.delete()
        return {"deleted": True, "id": category_id}

    @mcp.tool()
    def list_tags() -> list[dict[str, Any]]:
        """List tags defined in the current workspace."""
        ws = ctx.require_workspace()
        return [_tag(t) for t in Tag.objects.for_workspace(ws.id)]

    @mcp.tool()
    def create_tag(name: str) -> dict[str, Any]:
        """Create a tag (no-op if a tag with the same name exists)."""
        ws = ctx.require_workspace()
        t, _ = Tag.objects.get_or_create(workspace=ws, name=name)
        return _tag(t)
