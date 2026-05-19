"""list_approval_queue, approve, request_changes."""

from __future__ import annotations

from typing import Any

from apps.approvals import services as approvals_services
from apps.approvals.models import ApprovalAction, PostComment
from apps.composer.models import PlatformPost, Post


def _post_brief(post: Post) -> dict[str, Any]:
    return {
        "id": str(post.id),
        "title": post.title,
        "caption": post.caption[:280],
        "author_id": str(post.author_id) if post.author_id else None,
        "status": post.status,
        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
        "platform_posts": [
            {
                "id": str(pp.id),
                "platform": pp.social_account.platform,
                "social_account_id": str(pp.social_account_id),
                "status": pp.status,
            }
            for pp in post.platform_posts.select_related("social_account").all()
        ],
    }


def _resolve_target(ctx, target_id: str):
    """target_id may be a Post id or a PlatformPost id."""
    ws = ctx.require_workspace()
    post = Post.objects.for_workspace(ws.id).filter(pk=target_id).first()
    if post is not None:
        return post
    pp = (
        PlatformPost.objects.filter(pk=target_id, post__workspace_id=ws.id)
        .select_related("post", "social_account")
        .first()
    )
    if pp is not None:
        return pp
    raise ValueError(f"Post or PlatformPost {target_id} not found in current workspace.")


def register(mcp, ctx):
    @mcp.tool()
    def list_approval_queue(role: str = "internal") -> list[dict[str, Any]]:
        """List posts pending approval.

        role: 'internal' (pending_review) or 'client' (pending_client).
        """
        ctx.require_permission("approve_posts")
        ws = ctx.require_workspace()
        target_status = PlatformPost.Status.PENDING_CLIENT if role == "client" else PlatformPost.Status.PENDING_REVIEW
        qs = (
            Post.objects.for_workspace(ws.id)
            .filter(platform_posts__status=target_status)
            .distinct()
            .select_related("author")
            .prefetch_related("platform_posts__social_account")
            .order_by("scheduled_at", "-created_at")
        )
        return [_post_brief(p) for p in qs]

    @mcp.tool()
    def approve(target_id: str, comment: str = "") -> dict[str, Any]:
        """Approve a post or one platform variant. Accepts a post_id or platform_post_id."""
        ctx.require_permission("approve_posts")
        target = _resolve_target(ctx, target_id)
        post = approvals_services.approve_post(target, ctx.user, ctx.require_workspace(), comment=comment)
        return _post_brief(post)

    @mcp.tool()
    def request_changes(target_id: str, comment: str) -> dict[str, Any]:
        """Send a post (or one platform variant) back to the author with a comment."""
        ctx.require_permission("approve_posts")
        if not comment.strip():
            raise ValueError("A comment is required when requesting changes.")
        target = _resolve_target(ctx, target_id)
        post = approvals_services.request_changes(target, ctx.user, ctx.require_workspace(), comment)
        return _post_brief(post)

    @mcp.tool()
    def reject(target_id: str, comment: str) -> dict[str, Any]:
        """Reject a post or one platform variant outright with a comment."""
        ctx.require_permission("approve_posts")
        if not comment.strip():
            raise ValueError("A comment is required when rejecting.")
        target = _resolve_target(ctx, target_id)
        post = approvals_services.reject_post(target, ctx.user, ctx.require_workspace(), comment)
        return _post_brief(post)

    @mcp.tool()
    def submit_for_review(target_id: str) -> dict[str, Any]:
        """Submit a draft for internal review."""
        ctx.require_permission("create_posts")
        target = _resolve_target(ctx, target_id)
        post = approvals_services.submit_for_review(target, ctx.user, ctx.require_workspace())
        return _post_brief(post)

    @mcp.tool()
    def resubmit(target_id: str) -> dict[str, Any]:
        """Resubmit a changes_requested post for review."""
        ctx.require_permission("create_posts")
        target = _resolve_target(ctx, target_id)
        post = approvals_services.resubmit_post(target, ctx.user, ctx.require_workspace())
        return _post_brief(post)

    @mcp.tool()
    def bulk_approve(post_ids: list[str]) -> list[dict[str, Any]]:
        """Approve many posts in one call. Returns per-post success/error."""
        ctx.require_permission("approve_posts")
        results = approvals_services.bulk_approve(post_ids, ctx.user, ctx.require_workspace())
        return [{"post_id": pid, "success": ok, "error": err} for pid, ok, err in results]

    @mcp.tool()
    def bulk_reject(post_ids: list[str], comment: str) -> list[dict[str, Any]]:
        """Reject many posts with the same comment."""
        ctx.require_permission("approve_posts")
        if not comment.strip():
            raise ValueError("A comment is required when rejecting.")
        results = approvals_services.bulk_reject(post_ids, ctx.user, ctx.require_workspace(), comment)
        return [{"post_id": pid, "success": ok, "error": err} for pid, ok, err in results]

    @mcp.tool()
    def list_post_comments(post_id: str) -> list[dict[str, Any]]:
        """List comments on a post."""
        ws = ctx.require_workspace()
        post = Post.objects.for_workspace(ws.id).filter(pk=post_id).first()
        if post is None:
            raise ValueError(f"Post {post_id} not found in current workspace.")
        return [
            {
                "id": str(c.id),
                "author_id": str(c.author_id) if c.author_id else None,
                "body": c.body,
                "visibility": c.visibility,
                "parent_comment_id": str(c.parent_comment_id) if c.parent_comment_id else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in post.comments.filter(deleted_at__isnull=True).select_related("author").order_by("created_at")
        ]

    @mcp.tool()
    def add_post_comment(
        post_id: str,
        body: str,
        visibility: str = "internal",
        parent_comment_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a comment to a post. visibility: 'internal' | 'client'."""
        ws = ctx.require_workspace()
        post = Post.objects.for_workspace(ws.id).filter(pk=post_id).first()
        if post is None:
            raise ValueError(f"Post {post_id} not found in current workspace.")
        comment = PostComment.objects.create(
            post=post,
            author=ctx.user,
            body=body,
            visibility=visibility,
            parent_comment_id=parent_comment_id or None,
        )
        return {
            "id": str(comment.id),
            "post_id": str(post.id),
            "body": comment.body,
            "visibility": comment.visibility,
        }

    @mcp.tool()
    def list_approval_actions(post_id: str) -> list[dict[str, Any]]:
        """Return the audit history of approval actions for a post."""
        ctx.require_permission("approve_posts")
        ws = ctx.require_workspace()
        post = Post.objects.for_workspace(ws.id).filter(pk=post_id).first()
        if post is None:
            raise ValueError(f"Post {post_id} not found in current workspace.")
        return [
            {
                "id": str(a.id),
                "action": a.action,
                "user_id": str(a.user_id) if a.user_id else None,
                "platform_post_id": str(a.platform_post_id) if a.platform_post_id else None,
                "comment": a.comment,
                "created_at": a.created_at.isoformat(),
            }
            for a in ApprovalAction.objects.filter(post=post).order_by("created_at")
        ]
