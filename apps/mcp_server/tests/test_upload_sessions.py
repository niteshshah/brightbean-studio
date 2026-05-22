"""Tests for the chunked-upload helpers used by the media MCP tools."""

from __future__ import annotations

import base64

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import User
from apps.mcp_server import upload_sessions
from apps.media_library.models import MediaAsset, MediaFolder
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

# A 1x1 transparent PNG — small but a real, decodable image so create_asset's
# magic-byte sniffing classifies it as image/png instead of rejecting it.
PNG_BYTES = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _split_into_chunks(data: bytes, chunk_size: int) -> list[bytes]:
    return [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]


@pytest.mark.django_db
def test_chunked_upload_round_trip(mcp_workspace, mcp_user):
    started = upload_sessions.begin(
        workspace=mcp_workspace,
        user=mcp_user,
        filename="pixel.png",
        mime_type="image/png",
        total_size=len(PNG_BYTES),
    )
    session_id = started["session_id"]
    assert started["max_chunk_bytes"] > 0

    for seq, chunk in enumerate(_split_into_chunks(PNG_BYTES, chunk_size=20)):
        progress = upload_sessions.append(
            workspace=mcp_workspace,
            user=mcp_user,
            session_id=session_id,
            content_b64=base64.b64encode(chunk).decode("ascii"),
            sequence=seq,
        )
        assert progress["chunks_received"] == seq + 1

    asset = upload_sessions.finish(workspace=mcp_workspace, user=mcp_user, session_id=session_id)

    assert isinstance(asset, MediaAsset)
    assert asset.filename == "pixel.png"
    assert asset.file_size == len(PNG_BYTES)
    assert asset.workspace_id == mcp_workspace.id
    # Session must be gone after finalization.
    with pytest.raises(ValueError):
        upload_sessions.finish(workspace=mcp_workspace, user=mcp_user, session_id=session_id)


@pytest.mark.django_db
def test_append_rejects_out_of_order_sequence(mcp_workspace, mcp_user):
    session_id = upload_sessions.begin(
        workspace=mcp_workspace, user=mcp_user, filename="pixel.png", mime_type="image/png"
    )["session_id"]
    upload_sessions.append(
        workspace=mcp_workspace,
        user=mcp_user,
        session_id=session_id,
        content_b64=base64.b64encode(PNG_BYTES[:30]).decode("ascii"),
        sequence=0,
    )
    with pytest.raises(ValueError, match="Out-of-order"):
        upload_sessions.append(
            workspace=mcp_workspace,
            user=mcp_user,
            session_id=session_id,
            content_b64=base64.b64encode(PNG_BYTES[30:]).decode("ascii"),
            sequence=5,
        )


@pytest.mark.django_db
def test_session_is_scoped_to_owner_workspace(mcp_workspace, mcp_user, mcp_org):
    """A session_id from workspace A must not be usable from workspace B or another user."""
    other_ws = Workspace.objects.create(organization=mcp_org, name="Other WS")
    WorkspaceMembership.objects.create(
        user=mcp_user,
        workspace=other_ws,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )
    intruder = User.objects.create_user(
        email="intruder@example.com",
        password="x" * 12,
        name="Intruder",
        tos_accepted_at=timezone.now(),
    )
    OrgMembership.objects.create(user=intruder, organization=mcp_org, org_role=OrgMembership.OrgRole.MEMBER)
    WorkspaceMembership.objects.create(
        user=intruder,
        workspace=mcp_workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.EDITOR,
    )

    session_id = upload_sessions.begin(
        workspace=mcp_workspace, user=mcp_user, filename="pixel.png", mime_type="image/png"
    )["session_id"]

    with pytest.raises(ValueError, match="not found or expired"):
        upload_sessions.append(
            workspace=other_ws,
            user=mcp_user,
            session_id=session_id,
            content_b64=base64.b64encode(PNG_BYTES).decode("ascii"),
            sequence=0,
        )
    with pytest.raises(ValueError, match="not found or expired"):
        upload_sessions.append(
            workspace=mcp_workspace,
            user=intruder,
            session_id=session_id,
            content_b64=base64.b64encode(PNG_BYTES).decode("ascii"),
            sequence=0,
        )


@pytest.mark.django_db
def test_total_size_mismatch_blocks_finalize(mcp_workspace, mcp_user):
    session_id = upload_sessions.begin(
        workspace=mcp_workspace,
        user=mcp_user,
        filename="pixel.png",
        mime_type="image/png",
        total_size=len(PNG_BYTES),
    )["session_id"]
    upload_sessions.append(
        workspace=mcp_workspace,
        user=mcp_user,
        session_id=session_id,
        content_b64=base64.b64encode(PNG_BYTES[:30]).decode("ascii"),
        sequence=0,
    )
    with pytest.raises(ValueError, match="size mismatch"):
        upload_sessions.finish(workspace=mcp_workspace, user=mcp_user, session_id=session_id)


@pytest.mark.django_db
def test_chunk_size_limit_is_enforced(mcp_workspace, mcp_user, monkeypatch):
    monkeypatch.setattr(upload_sessions, "MAX_CHUNK_BYTES", 32)
    session_id = upload_sessions.begin(
        workspace=mcp_workspace, user=mcp_user, filename="pixel.png", mime_type="image/png"
    )["session_id"]
    too_big = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(ValueError, match="exceeds maximum"):
        upload_sessions.append(
            workspace=mcp_workspace,
            user=mcp_user,
            session_id=session_id,
            content_b64=too_big,
            sequence=0,
        )


@pytest.mark.django_db
def test_cancel_clears_session(mcp_workspace, mcp_user):
    session_id = upload_sessions.begin(
        workspace=mcp_workspace, user=mcp_user, filename="pixel.png", mime_type="image/png"
    )["session_id"]
    assert upload_sessions.cancel(workspace=mcp_workspace, user=mcp_user, session_id=session_id) is True
    assert upload_sessions.cancel(workspace=mcp_workspace, user=mcp_user, session_id=session_id) is False
    with pytest.raises(ValueError, match="not found or expired"):
        upload_sessions.finish(workspace=mcp_workspace, user=mcp_user, session_id=session_id)


@pytest.mark.django_db
def test_begin_rejects_unknown_folder(mcp_workspace, mcp_user):
    with pytest.raises(ValueError, match="Folder"):
        upload_sessions.begin(
            workspace=mcp_workspace,
            user=mcp_user,
            filename="pixel.png",
            mime_type="image/png",
            folder_id="00000000-0000-0000-0000-000000000000",
        )


@pytest.mark.django_db
def test_finalize_places_asset_in_folder(mcp_workspace, mcp_user):
    folder = MediaFolder.objects.create(organization=mcp_workspace.organization, workspace=mcp_workspace, name="Drafts")
    session_id = upload_sessions.begin(
        workspace=mcp_workspace,
        user=mcp_user,
        filename="pixel.png",
        mime_type="image/png",
        folder_id=str(folder.id),
    )["session_id"]
    upload_sessions.append(
        workspace=mcp_workspace,
        user=mcp_user,
        session_id=session_id,
        content_b64=base64.b64encode(PNG_BYTES).decode("ascii"),
        sequence=0,
    )
    asset = upload_sessions.finish(workspace=mcp_workspace, user=mcp_user, session_id=session_id)
    assert asset.folder_id == folder.id
