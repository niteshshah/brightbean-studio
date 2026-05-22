"""Chunked media upload sessions for the MCP server.

A single MCP tool call has a practical limit on argument size — passing a
multi-megabyte base64 payload through one call is unreliable and often hits
context/transport limits. These helpers let an MCP client stream a file
across many small tool calls:

    session_id = begin(workspace, user, filename, ...)
    append(workspace, user, session_id, chunk_b64, sequence=0)
    append(workspace, user, session_id, chunk_b64, sequence=1)
    ...
    asset = finish(workspace, user, session_id)

Sessions live in Django's default cache (Redis in production, locmem in dev)
keyed by a UUID. They auto-expire after ``SESSION_TTL_SECONDS`` of inactivity
and are bound to the workspace + user that started them, so a leaked
session_id can't be used cross-tenant.
"""

from __future__ import annotations

import secrets
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.media_library.models import MediaAsset, MediaFolder
from apps.media_library.services import create_asset

SESSION_TTL_SECONDS = 60 * 60  # 1 hour of inactivity before a session is dropped
MAX_CHUNK_BYTES = 1 * 1024 * 1024  # 1 MiB raw per append (~1.4 MiB base64)
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # hard ceiling, regardless of media type


def _max_total_bytes() -> int:
    """Upper bound on a single upload — the larger of the image / video limits."""
    image_cap = getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_SIZE", 20 * 1024 * 1024)
    video_cap = getattr(settings, "MEDIA_LIBRARY_MAX_VIDEO_SIZE", 1024 * 1024 * 1024)
    return max(image_cap, video_cap, DEFAULT_MAX_TOTAL_BYTES)


def _cache_key(session_id: str) -> str:
    return f"mcp_upload_session:{session_id}"


def _load(session_id: str, *, workspace_id, user_id) -> dict[str, Any]:
    state = cache.get(_cache_key(session_id))
    if state is None:
        raise ValueError(f"Upload session {session_id} not found or expired.")
    if state["workspace_id"] != str(workspace_id) or state["user_id"] != user_id:
        # Same error for "not found" and "wrong owner" so we don't leak existence.
        raise ValueError(f"Upload session {session_id} not found or expired.")
    return state


def _save(session_id: str, state: dict[str, Any]) -> None:
    cache.set(_cache_key(session_id), state, timeout=SESSION_TTL_SECONDS)


def begin(
    *,
    workspace,
    user,
    filename: str,
    mime_type: str = "",
    total_size: int | None = None,
    folder_id: str | None = None,
) -> dict[str, Any]:
    """Open a new chunked upload session.

    ``total_size`` is optional — if supplied we reject it upfront when it
    already exceeds the configured maximum, sparing the caller a round-trip
    of doomed chunk uploads.
    """
    if not filename:
        raise ValueError("filename is required.")

    max_total = _max_total_bytes()
    if total_size is not None:
        if total_size <= 0:
            raise ValueError("total_size must be positive.")
        if total_size > max_total:
            raise ValueError(f"total_size {total_size} exceeds maximum {max_total} bytes.")

    if folder_id:
        folder_exists = MediaFolder.objects.filter(id=folder_id, workspace_id=workspace.id).exists()
        if not folder_exists:
            raise ValueError(f"Folder {folder_id} not found in this workspace.")

    session_id = secrets.token_urlsafe(24)
    state = {
        "workspace_id": str(workspace.id),
        "user_id": user.id,
        "filename": filename,
        "mime_type": mime_type or "",
        "folder_id": str(folder_id) if folder_id else None,
        "total_size": int(total_size) if total_size is not None else None,
        "data": b"",
        "next_sequence": 0,
    }
    _save(session_id, state)
    return {
        "session_id": session_id,
        "max_chunk_bytes": MAX_CHUNK_BYTES,
        "max_total_bytes": max_total,
        "expires_in_seconds": SESSION_TTL_SECONDS,
    }


def append(
    *,
    workspace,
    user,
    session_id: str,
    content_b64: str,
    sequence: int,
) -> dict[str, Any]:
    """Append one base64-encoded chunk. ``sequence`` must increment from 0."""
    import base64

    state = _load(session_id, workspace_id=workspace.id, user_id=user.id)

    if sequence != state["next_sequence"]:
        raise ValueError(f"Out-of-order chunk: expected sequence {state['next_sequence']}, got {sequence}.")

    try:
        raw = base64.b64decode(content_b64, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 payload: {exc}") from exc

    if len(raw) == 0:
        raise ValueError("Chunk is empty.")
    if len(raw) > MAX_CHUNK_BYTES:
        raise ValueError(f"Chunk size {len(raw)} exceeds maximum {MAX_CHUNK_BYTES} bytes per call.")

    new_total = len(state["data"]) + len(raw)
    max_total = _max_total_bytes()
    if new_total > max_total:
        cache.delete(_cache_key(session_id))
        raise ValueError(f"Upload would exceed maximum total size of {max_total} bytes.")
    if state["total_size"] is not None and new_total > state["total_size"]:
        raise ValueError(f"Upload exceeds declared total_size {state['total_size']} (got {new_total}).")

    state["data"] += raw
    state["next_sequence"] = sequence + 1
    _save(session_id, state)

    return {
        "session_id": session_id,
        "bytes_received": new_total,
        "chunks_received": state["next_sequence"],
    }


def finish(*, workspace, user, session_id: str) -> MediaAsset:
    """Assemble the buffered chunks into a MediaAsset and drop the session."""
    state = _load(session_id, workspace_id=workspace.id, user_id=user.id)

    data = state["data"]
    if not data:
        raise ValueError("Cannot finalize an empty upload session.")
    if state["total_size"] is not None and len(data) != state["total_size"]:
        raise ValueError(f"Upload size mismatch: received {len(data)} bytes but declared {state['total_size']}.")

    upload = SimpleUploadedFile(
        state["filename"],
        data,
        content_type=state["mime_type"] or "application/octet-stream",
    )

    folder = None
    folder_id = state.get("folder_id")
    if folder_id:
        folder = MediaFolder.objects.filter(id=folder_id, workspace_id=workspace.id).first()

    asset = create_asset(
        organization=workspace.organization,
        workspace=workspace,
        uploaded_file=upload,
        uploaded_by=user,
        folder=folder,
    )
    cache.delete(_cache_key(session_id))
    return asset


def cancel(*, workspace, user, session_id: str) -> bool:
    """Drop an in-progress session. Returns True if a session was cleared."""
    state = cache.get(_cache_key(session_id))
    if state is None:
        return False
    if state["workspace_id"] != str(workspace.id) or state["user_id"] != user.id:
        return False
    cache.delete(_cache_key(session_id))
    return True
