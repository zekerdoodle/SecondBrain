"""
Moltbook API Tools.

Tools for interacting with the Moltbook AI social platform.
API docs: https://www.moltbook.com/developers
"""

import os
from typing import Any, Dict, Optional

import httpx
from claude_agent_sdk import tool

from ..registry import register_tool

# Base URL for Moltbook API
MOLTBOOK_BASE_URL = "https://www.moltbook.com/api/v1"
SECRETS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), ".secrets", "moltbook.env")


def _get_api_key() -> str:
    """Load API key from secrets file at execution time."""
    if not os.path.exists(SECRETS_PATH):
        raise ValueError(f"Moltbook secrets file not found: {SECRETS_PATH}")

    with open(SECRETS_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("MOLTBOOK_API_KEY="):
                return line.split("=", 1)[1].strip()

    raise ValueError("MOLTBOOK_API_KEY not found in secrets file")


def _get_headers() -> Dict[str, str]:
    """Get headers for Moltbook API requests."""
    return {
        "X-API-Key": _get_api_key(),
        "Content-Type": "application/json",
    }


async def _make_request(
    method: str,
    endpoint: str,
    params: Optional[Dict] = None,
    json_data: Optional[Dict] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Make a request to the Moltbook API."""
    url = f"{MOLTBOOK_BASE_URL}{endpoint}"
    headers = _get_headers()

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=timeout,
        )

        if response.status_code >= 400:
            error_text = response.text
            raise Exception(f"Moltbook API error ({response.status_code}): {error_text}")

        return response.json()


@register_tool("moltbook")
@tool(
    name="moltbook_feed",
    description="""Get posts from a Moltbook feed.

Moltbook is a Reddit-like social platform for AI agents. Use this to browse posts and see what other agents are discussing.

Sort options:
- hot: Popular posts (default)
- new: Most recent posts
- top: Highest voted posts
- rising: Gaining momentum

Returns a list of posts with id, title, content/url, author, votes, comment count, and submolt.""",
    input_schema={
        "type": "object",
        "properties": {
            "sort": {
                "type": "string",
                "description": "Sort order: hot, new, top, rising (default: hot)",
                "enum": ["hot", "new", "top", "rising"],
                "default": "hot"
            },
            "submolt": {
                "type": "string",
                "description": "Filter to a specific submolt/community (optional)"
            },
            "limit": {
                "type": "integer",
                "description": "Number of posts to return (default: 25, max: 100)",
                "default": 25
            }
        }
    }
)
async def moltbook_feed(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get posts from a Moltbook feed."""
    try:
        sort = args.get("sort", "hot")
        submolt = args.get("submolt")
        limit = min(args.get("limit", 25), 100)

        params = {"sort": sort, "limit": limit}

        # Determine endpoint based on submolt
        if submolt:
            endpoint = f"/submolts/{submolt}/posts"
        else:
            endpoint = "/posts"

        data = await _make_request("GET", endpoint, params=params)

        posts = data.get("posts", data) if isinstance(data, dict) else data

        if not posts:
            return {"content": [{"type": "text", "text": f"No posts found (sort: {sort}, submolt: {submolt or 'all'})"}]}

        output = [f"## Moltbook Feed ({sort})\n"]
        if submolt:
            output.append(f"**Submolt:** m/{submolt}\n")
        output.append(f"**{len(posts)} posts**\n")

        for post in posts:
            post_id = post.get("id", "unknown")
            title = post.get("title", "Untitled")
            author = post.get("author", post.get("agent_name", "unknown"))
            votes = post.get("score", post.get("votes", 0))
            comments = post.get("comment_count", post.get("num_comments", 0))
            post_submolt = post.get("submolt", post.get("subreddit", "general"))
            content = post.get("content", post.get("body", ""))
            url = post.get("url")

            output.append(f"---\n**ID:** `{post_id}` | **m/{post_submolt}** | {votes} points | {comments} comments")
            output.append(f"**{title}**")
            output.append(f"*by {author}*")
            if url:
                output.append(f"Link: {url}")
            elif content:
                preview = content[:300] + "..." if len(content) > 300 else content
                output.append(preview)
            output.append("")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_post",
    description="""Create a new post on Moltbook.

Create a text post or link post in a submolt (community). Text posts have a body, link posts have a URL.

Common submolts: general, aithoughts, tech, science, programming""",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Post title (required)"
            },
            "submolt": {
                "type": "string",
                "description": "Submolt to post in (default: general)",
                "default": "general"
            },
            "content": {
                "type": "string",
                "description": "Text content for a text post"
            },
            "url": {
                "type": "string",
                "description": "URL for a link post"
            }
        },
        "required": ["title"]
    }
)
async def moltbook_post(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new post on Moltbook."""
    try:
        title = args.get("title", "").strip()
        submolt = args.get("submolt", "general")
        content = args.get("content", "").strip()
        url = args.get("url", "").strip()

        if not title:
            return {"content": [{"type": "text", "text": "Error: title is required"}], "is_error": True}

        if not content and not url:
            return {"content": [{"type": "text", "text": "Error: either content (text post) or url (link post) is required"}], "is_error": True}

        post_data = {
            "title": title,
            "submolt": submolt,
        }

        if url:
            post_data["url"] = url
        else:
            post_data["content"] = content

        result = await _make_request("POST", "/posts", json_data=post_data)

        post_id = result.get("id", result.get("post_id", "unknown"))
        post_url = result.get("url", f"https://www.moltbook.com/m/{submolt}/posts/{post_id}")

        return {"content": [{"type": "text", "text": f"Post created successfully!\n\n**ID:** `{post_id}`\n**Title:** {title}\n**Submolt:** m/{submolt}\n**URL:** {post_url}"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_comment",
    description="""Comment on a Moltbook post or reply to a comment.

Add a comment to a post, or reply to an existing comment by providing the parent_comment_id.""",
    input_schema={
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "ID of the post to comment on"
            },
            "content": {
                "type": "string",
                "description": "Comment text"
            },
            "parent_comment_id": {
                "type": "string",
                "description": "ID of parent comment for a reply (optional)"
            }
        },
        "required": ["post_id", "content"]
    }
)
async def moltbook_comment(args: Dict[str, Any]) -> Dict[str, Any]:
    """Comment on a post or reply to a comment."""
    try:
        post_id = args.get("post_id", "").strip()
        content = args.get("content", "").strip()
        parent_id = args.get("parent_comment_id", "").strip()

        if not post_id or not content:
            return {"content": [{"type": "text", "text": "Error: post_id and content are required"}], "is_error": True}

        comment_data = {"content": content}
        if parent_id:
            comment_data["parent_id"] = parent_id

        result = await _make_request("POST", f"/posts/{post_id}/comments", json_data=comment_data)

        comment_id = result.get("id", result.get("comment_id", "unknown"))

        reply_note = f" (reply to `{parent_id}`)" if parent_id else ""
        return {"content": [{"type": "text", "text": f"Comment posted{reply_note}!\n\n**Comment ID:** `{comment_id}`\n**Post ID:** `{post_id}`\n**Content:** {content[:200]}{'...' if len(content) > 200 else ''}"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_get_post",
    description="""Get a specific Moltbook post with its comments.

Retrieves full post details and threaded comments. Use this after browsing the feed to read a post in detail.""",
    input_schema={
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "ID of the post to retrieve"
            },
            "comment_sort": {
                "type": "string",
                "description": "Sort order for comments: top, new, hot (default: top)",
                "enum": ["top", "new", "hot"],
                "default": "top"
            }
        },
        "required": ["post_id"]
    }
)
async def moltbook_get_post(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get a post with its comments."""
    try:
        post_id = args.get("post_id", "").strip()
        comment_sort = args.get("comment_sort", "top")

        if not post_id:
            return {"content": [{"type": "text", "text": "Error: post_id is required"}], "is_error": True}

        # Get the post
        post = await _make_request("GET", f"/posts/{post_id}")

        # Get comments
        comments_data = await _make_request(
            "GET",
            f"/posts/{post_id}/comments",
            params={"sort": comment_sort}
        )
        comments = comments_data.get("comments", comments_data) if isinstance(comments_data, dict) else comments_data

        # Format output
        title = post.get("title", "Untitled")
        author = post.get("author", post.get("agent_name", "unknown"))
        votes = post.get("score", post.get("votes", 0))
        comment_count = post.get("comment_count", post.get("num_comments", 0))
        submolt = post.get("submolt", post.get("subreddit", "general"))
        content = post.get("content", post.get("body", ""))
        url = post.get("url")
        created = post.get("created_at", post.get("created", ""))

        output = [f"## {title}\n"]
        output.append(f"**m/{submolt}** | Posted by *{author}* | {votes} points | {comment_count} comments")
        if created:
            output.append(f"**Created:** {created}")
        output.append("")

        if url:
            output.append(f"**Link:** {url}\n")

        if content:
            output.append(content)
            output.append("")

        output.append("---\n## Comments\n")

        def format_comment(comment: Dict, depth: int = 0) -> str:
            """Format a comment with indentation for threading."""
            indent = "  " * depth
            author = comment.get("author", comment.get("agent_name", "unknown"))
            text = comment.get("content", comment.get("body", ""))
            votes = comment.get("score", comment.get("votes", 0))
            cid = comment.get("id", "")

            lines = [f"{indent}**{author}** ({votes} points) `{cid}`"]
            lines.append(f"{indent}{text}")

            # Handle nested replies
            replies = comment.get("replies", comment.get("children", []))
            if replies:
                for reply in replies:
                    lines.append(format_comment(reply, depth + 1))

            return "\n".join(lines)

        if comments:
            for comment in comments[:50]:  # Limit to 50 top-level comments
                output.append(format_comment(comment))
                output.append("")
        else:
            output.append("*No comments yet*")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_notifications",
    description="""Check Moltbook notifications and replies.

See replies to your posts and comments, mentions, and other notifications.""",
    input_schema={
        "type": "object",
        "properties": {
            "unread_only": {
                "type": "boolean",
                "description": "Only show unread notifications (default: false)",
                "default": False
            },
            "limit": {
                "type": "integer",
                "description": "Number of notifications to return (default: 25)",
                "default": 25
            }
        }
    }
)
async def moltbook_notifications(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check notifications and replies."""
    try:
        unread_only = args.get("unread_only", False)
        limit = args.get("limit", 25)

        params = {"limit": limit}
        if unread_only:
            params["unread"] = "true"

        # Try common notification endpoint patterns
        try:
            data = await _make_request("GET", "/notifications", params=params)
        except Exception:
            # Fallback: try /agents/me/notifications
            try:
                data = await _make_request("GET", "/agents/me/notifications", params=params)
            except Exception:
                # Another fallback: try /inbox
                data = await _make_request("GET", "/inbox", params=params)

        notifications = data.get("notifications", data.get("items", data)) if isinstance(data, dict) else data

        if not notifications:
            return {"content": [{"type": "text", "text": "No notifications found."}]}

        output = ["## Moltbook Notifications\n"]
        if unread_only:
            output.append("*Showing unread only*\n")

        for notif in notifications:
            notif_type = notif.get("type", "notification")
            is_read = notif.get("read", notif.get("is_read", False))
            created = notif.get("created_at", notif.get("created", ""))

            read_marker = "" if is_read else " [UNREAD]"

            # Format based on notification type
            if notif_type == "comment_reply" or notif_type == "reply":
                author = notif.get("author", notif.get("from", "unknown"))
                content = notif.get("content", notif.get("body", ""))[:200]
                post_id = notif.get("post_id", "")
                output.append(f"**Reply from {author}**{read_marker}")
                output.append(f"{content}")
                if post_id:
                    output.append(f"Post ID: `{post_id}`")
            elif notif_type == "mention":
                author = notif.get("author", notif.get("from", "unknown"))
                content = notif.get("content", notif.get("body", ""))[:200]
                output.append(f"**Mentioned by {author}**{read_marker}")
                output.append(f"{content}")
            elif notif_type == "post_reply":
                author = notif.get("author", notif.get("from", "unknown"))
                content = notif.get("content", notif.get("body", ""))[:200]
                post_title = notif.get("post_title", "")
                output.append(f"**Comment on your post by {author}**{read_marker}")
                if post_title:
                    output.append(f"Post: {post_title}")
                output.append(f"{content}")
            else:
                # Generic notification
                message = notif.get("message", notif.get("content", str(notif)))
                output.append(f"**{notif_type}**{read_marker}")
                output.append(f"{message[:200]}")

            if created:
                output.append(f"*{created}*")
            output.append("---")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
