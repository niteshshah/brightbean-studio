"""Members & invitations: list_members, list_invitations, create_invitation,
resend_invitation, revoke_invitation, update_member_role, remove_member."""

from __future__ import annotations

from typing import Any

from apps.members import services as members_services
from apps.members.models import Invitation, OrgMembership, WorkspaceMembership


def _org_member(m: OrgMembership) -> dict[str, Any]:
    return {
        "user_id": str(m.user_id),
        "email": m.user.email,
        "name": m.user.display_name,
        "org_role": m.org_role,
        "invited_at": m.invited_at.isoformat(),
    }


def _ws_member(m: WorkspaceMembership) -> dict[str, Any]:
    return {
        "user_id": str(m.user_id),
        "email": m.user.email,
        "name": m.user.display_name,
        "workspace_role": m.custom_role.name if m.custom_role else m.workspace_role,
        "added_at": m.added_at.isoformat(),
    }


def _invitation(inv: Invitation) -> dict[str, Any]:
    return {
        "id": str(inv.id),
        "email": inv.email,
        "org_role": inv.org_role,
        "workspace_assignments": inv.workspace_assignments,
        "expires_at": inv.expires_at.isoformat(),
        "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
        "is_expired": inv.is_expired,
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_members() -> dict[str, Any]:
        """List org and workspace memberships for the current workspace's organization."""
        if ctx.organization is None:
            raise ValueError("No organization context.")
        org_members = OrgMembership.objects.filter(organization=ctx.organization).select_related("user")
        ws = ctx.current_workspace
        ws_members = (
            WorkspaceMembership.objects.filter(workspace=ws).select_related("user", "custom_role") if ws else []
        )
        return {
            "organization_id": str(ctx.organization.id),
            "org_members": [_org_member(m) for m in org_members],
            "workspace_members": [_ws_member(m) for m in ws_members],
        }

    @mcp.tool()
    def list_invitations() -> list[dict[str, Any]]:
        """List pending invitations for the current organization."""
        if ctx.organization is None:
            raise ValueError("No organization context.")
        invs = Invitation.objects.filter(organization=ctx.organization).order_by("-created_at")
        return [_invitation(i) for i in invs]

    @mcp.tool()
    def create_invitation(
        email: str,
        org_role: str = "member",
        workspace_assignments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Invite an email into the current organization.

        org_role: 'member' | 'admin'. workspace_assignments: list of
        {"workspace_id": "...", "role": "owner|manager|editor|contributor|client|viewer"}.
        """
        if ctx.organization is None:
            raise ValueError("No organization context.")
        inv = members_services.create_invitation(
            ctx.organization,
            email,
            org_role,
            list(workspace_assignments or []),
            invited_by=ctx.user,
            inviter=ctx.user,
        )
        return _invitation(inv)

    @mcp.tool()
    def resend_invitation(invitation_id: str) -> dict[str, Any]:
        if ctx.organization is None:
            raise ValueError("No organization context.")
        inv = Invitation.objects.filter(organization=ctx.organization, pk=invitation_id).first()
        if inv is None:
            raise ValueError(f"Invitation {invitation_id} not found.")
        members_services.resend_invitation(inv)
        inv.refresh_from_db()
        return _invitation(inv)

    @mcp.tool()
    def revoke_invitation(invitation_id: str) -> dict[str, Any]:
        if ctx.organization is None:
            raise ValueError("No organization context.")
        inv = Invitation.objects.filter(organization=ctx.organization, pk=invitation_id).first()
        if inv is None:
            raise ValueError(f"Invitation {invitation_id} not found.")
        members_services.revoke_invitation(inv)
        return {"revoked": True, "id": invitation_id}

    @mcp.tool()
    def update_member_role(
        user_id: str,
        workspace_id: str | None = None,
        org_role: str | None = None,
        workspace_assignments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update an org role and/or workspace role assignments for a user."""
        if ctx.organization is None:
            raise ValueError("No organization context.")
        org_membership = OrgMembership.objects.filter(organization=ctx.organization, user_id=user_id).first()
        if org_membership is None:
            raise ValueError(f"User {user_id} is not a member of this organization.")

        if org_role:
            members_services.update_member_org_role(ctx.organization, org_membership, org_role, caller=ctx.user)
        if workspace_assignments is not None:
            members_services.update_workspace_assignments(
                ctx.organization, org_membership.user, list(workspace_assignments), inviter=ctx.user
            )
        return {"user_id": user_id, "updated": True}

    @mcp.tool()
    def remove_member(user_id: str) -> dict[str, Any]:
        """Remove a member from the organization (and all their workspaces)."""
        if ctx.organization is None:
            raise ValueError("No organization context.")
        org_membership = OrgMembership.objects.filter(organization=ctx.organization, user_id=user_id).first()
        if org_membership is None:
            raise ValueError(f"User {user_id} is not a member of this organization.")
        members_services.remove_member(ctx.organization, org_membership, removed_by=ctx.user)
        return {"removed": True, "user_id": user_id}
