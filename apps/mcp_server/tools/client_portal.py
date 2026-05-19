"""Client portal magic links.

list_magic_links, generate_magic_link, revoke_magic_link.
"""

from __future__ import annotations

from typing import Any

from apps.accounts.models import User
from apps.client_portal.models import MagicLinkToken
from apps.client_portal.services import (
    generate_magic_link as _svc_generate,
)
from apps.client_portal.services import (
    revoke_magic_link as _svc_revoke,
)
from apps.members.models import WorkspaceMembership


def _serialize(t: MagicLinkToken) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "user_id": str(t.user_id),
        "user_email": t.user.email if t.user_id else None,
        "workspace_id": str(t.workspace_id),
        "created_at": t.created_at.isoformat(),
        "expires_at": t.expires_at.isoformat(),
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "is_consumed": t.is_consumed,
        "is_expired": t.is_expired,
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_magic_links() -> list[dict[str, Any]]:
        """List magic-link tokens for the current workspace."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        return [
            _serialize(t)
            for t in MagicLinkToken.objects.filter(workspace=ws).select_related("user").order_by("-created_at")
        ]

    @mcp.tool()
    def generate_magic_link(client_user_id: str) -> dict[str, Any]:
        """Generate (and email) a magic-link token for a client-role user."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        client_user = User.objects.filter(pk=client_user_id).first()
        if client_user is None:
            raise ValueError(f"User {client_user_id} not found.")
        if not WorkspaceMembership.objects.filter(
            user=client_user,
            workspace=ws,
            workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
        ).exists():
            raise ValueError("Target user does not have client role in this workspace.")
        token = _svc_generate(workspace=ws, client_user=client_user, created_by=ctx.user)
        return _serialize(token)

    @mcp.tool()
    def revoke_magic_link(token_id: str) -> dict[str, Any]:
        """Revoke an active magic-link token (idempotent)."""
        ctx.require_permission("manage_workspace_settings")
        ws = ctx.require_workspace()
        _svc_revoke(token_id, ws)
        return {"revoked": True, "id": token_id}
