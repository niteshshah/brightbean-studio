"""Media tools: list_media, get_media, upload (url/base64), folders, delete."""

from __future__ import annotations

import base64
import io
from typing import Any
from urllib.parse import urlparse

import httpx
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.mcp_server import upload_sessions
from apps.media_library.models import MediaAsset, MediaFolder
from apps.media_library.services import (
    ProtectedAssetError,
    create_asset,
    delete_asset,
)
from apps.media_library.services import create_folder as _svc_create_folder


def _serialize(asset: MediaAsset) -> dict[str, Any]:
    try:
        url = asset.file.url if asset.file else ""
    except Exception:
        url = ""
    return {
        "id": str(asset.id),
        "filename": asset.filename,
        "media_type": asset.media_type,
        "mime_type": asset.mime_type,
        "file_size": asset.file_size,
        "width": asset.width,
        "height": asset.height,
        "duration": asset.duration,
        "folder_id": str(asset.folder_id) if asset.folder_id else None,
        "is_starred": asset.is_starred,
        "url": url,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


def register(mcp, ctx):
    @mcp.tool()
    def list_media(
        folder_id: str | None = None,
        media_type: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List media assets in the current workspace (and shared org library).

        media_type: image | video | gif | document
        """
        ws = ctx.require_workspace()
        org_id = ws.organization_id
        qs = MediaAsset.objects.for_workspace_with_shared(ws.id, org_id).select_related("folder")
        if folder_id:
            qs = qs.filter(folder_id=folder_id)
        if media_type:
            qs = qs.filter(media_type=media_type)
        if search:
            qs = qs.filter(filename__icontains=search)
        qs = qs.order_by("-created_at")
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        total = qs.count()
        items = [_serialize(a) for a in qs[offset : offset + limit]]
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    @mcp.tool()
    def upload_media_from_url(
        url: str,
        filename: str | None = None,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Download an image/video from ``url`` and add it to the workspace media library."""
        ctx.require_permission("upload_media")
        ws = ctx.require_workspace()

        if not filename:
            path = urlparse(url).path
            filename = path.rsplit("/", 1)[-1] or "download.bin"

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.content

        upload = SimpleUploadedFile(filename, io.BytesIO(content).getvalue())

        folder = None
        if folder_id:
            from apps.media_library.models import MediaFolder

            folder = MediaFolder.objects.filter(id=folder_id, workspace_id=ws.id).first()

        asset = create_asset(
            organization=ws.organization,
            workspace=ws,
            uploaded_file=upload,
            uploaded_by=ctx.user,
            folder=folder,
        )
        return _serialize(asset)

    @mcp.tool()
    def get_media(asset_id: str) -> dict[str, Any]:
        """Return one media asset's metadata + URL."""
        ws = ctx.require_workspace()
        asset = MediaAsset.objects.for_workspace_with_shared(ws.id, ws.organization_id).filter(pk=asset_id).first()
        if asset is None:
            raise ValueError(f"MediaAsset {asset_id} not found.")
        return _serialize(asset)

    @mcp.tool()
    def upload_media_from_base64(
        filename: str,
        content_b64: str,
        mime_type: str = "",
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload an asset given its base64-encoded bytes.

        Suitable for small files only (rule of thumb: under ~200 KB raw / ~270 KB
        base64). For anything larger, use the chunked flow — begin_media_upload,
        append_media_upload_chunk, finish_media_upload — to avoid tool-call
        size limits.
        """
        ctx.require_permission("upload_media")
        ws = ctx.require_workspace()
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception as exc:
            raise ValueError(f"Invalid base64 payload: {exc}") from exc
        upload = SimpleUploadedFile(filename, raw, content_type=mime_type or "application/octet-stream")
        folder = None
        if folder_id:
            folder = MediaFolder.objects.filter(id=folder_id, workspace_id=ws.id).first()
        asset = create_asset(
            organization=ws.organization,
            workspace=ws,
            uploaded_file=upload,
            uploaded_by=ctx.user,
            folder=folder,
        )
        return _serialize(asset)

    @mcp.tool()
    def begin_media_upload(
        filename: str,
        mime_type: str = "",
        total_size: int | None = None,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Start a chunked upload session for a media file.

        Use this when the file is too large to send through a single
        upload_media_from_base64 call (rule of thumb: anything over ~200 KB raw).
        Returns ``session_id`` plus ``max_chunk_bytes`` (the largest raw chunk
        you may send per append_media_upload_chunk call). Pass ``total_size``
        when known so oversize uploads fail before any chunks are sent.
        """
        ctx.require_permission("upload_media")
        ws = ctx.require_workspace()
        return upload_sessions.begin(
            workspace=ws,
            user=ctx.user,
            filename=filename,
            mime_type=mime_type,
            total_size=total_size,
            folder_id=folder_id,
        )

    @mcp.tool()
    def append_media_upload_chunk(
        session_id: str,
        content_b64: str,
        sequence: int,
    ) -> dict[str, Any]:
        """Append one base64-encoded chunk to an in-progress upload session.

        ``sequence`` starts at 0 and must increment by 1 per call — out-of-order
        chunks are rejected. Each chunk's raw (decoded) size must not exceed
        the ``max_chunk_bytes`` returned by begin_media_upload.
        """
        ctx.require_permission("upload_media")
        ws = ctx.require_workspace()
        return upload_sessions.append(
            workspace=ws,
            user=ctx.user,
            session_id=session_id,
            content_b64=content_b64,
            sequence=sequence,
        )

    @mcp.tool()
    def finish_media_upload(session_id: str) -> dict[str, Any]:
        """Finalize an upload session and create the media asset."""
        ctx.require_permission("upload_media")
        ws = ctx.require_workspace()
        asset = upload_sessions.finish(workspace=ws, user=ctx.user, session_id=session_id)
        return _serialize(asset)

    @mcp.tool()
    def cancel_media_upload(session_id: str) -> dict[str, Any]:
        """Abandon an in-progress upload session and free its buffered bytes."""
        ws = ctx.require_workspace()
        cleared = upload_sessions.cancel(workspace=ws, user=ctx.user, session_id=session_id)
        return {"session_id": session_id, "cancelled": cleared}

    @mcp.tool()
    def delete_media(asset_id: str) -> dict[str, Any]:
        """Delete a media asset. Fails with a list of referencing posts if it is in use."""
        ctx.require_permission("delete_media")
        ws = ctx.require_workspace()
        asset = MediaAsset.objects.for_workspace_with_shared(ws.id, ws.organization_id).filter(pk=asset_id).first()
        if asset is None:
            raise ValueError(f"MediaAsset {asset_id} not found.")
        try:
            delete_asset(asset)
        except ProtectedAssetError as exc:
            return {"deleted": False, "id": asset_id, "blocked_by": getattr(exc, "referencing_posts", [])}
        return {"deleted": True, "id": asset_id}

    @mcp.tool()
    def list_folders(parent_id: str | None = None) -> list[dict[str, Any]]:
        """List media folders. Optional parent_id (None = top-level)."""
        ws = ctx.require_workspace()
        qs = MediaFolder.objects.filter(workspace_id=ws.id, parent_folder_id=parent_id or None).order_by("name")
        return [
            {
                "id": str(f.id),
                "name": f.name,
                "parent_folder_id": str(f.parent_folder_id) if f.parent_folder_id else None,
            }
            for f in qs
        ]

    @mcp.tool()
    def create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
        """Create a media folder (depth limited by the existing service)."""
        ctx.require_permission("manage_media")
        ws = ctx.require_workspace()
        parent = None
        if parent_id:
            parent = MediaFolder.objects.filter(id=parent_id, workspace_id=ws.id).first()
        folder = _svc_create_folder(organization=ws.organization, workspace=ws, name=name, parent_folder=parent)
        return {
            "id": str(folder.id),
            "name": folder.name,
            "parent_folder_id": str(folder.parent_folder_id) if folder.parent_folder_id else None,
        }
