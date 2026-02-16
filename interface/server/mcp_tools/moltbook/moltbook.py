"""
Moltbook API Tools.

Tools for interacting with the Moltbook AI social platform.
API docs: https://www.moltbook.com/developers

Includes verification challenge detection on every API response.
Challenges are undocumented — we scan for unknown fields, challenge-related
keywords, and suspicious response headers on every call.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger(__name__)

# Base URL for Moltbook API
MOLTBOOK_BASE_URL = "https://www.moltbook.com/api/v1"
SECRETS_PATH = "/home/debian/second_brain/.claude/.secrets/moltbook.env"
CHALLENGE_LOG_PATH = "/home/debian/second_brain/.claude/logs/moltbook_challenges.jsonl"

# Fields we EXPECT in Moltbook API responses, by endpoint pattern.
# Anything outside these sets is flagged as potentially challenge-related.
_KNOWN_TOPLEVEL_FIELDS = {
    "success", "message", "error", "hint",
    # Post responses
    "post", "posts",
    # Comment responses
    "comment", "comments",
    # Agent responses
    "agent", "agents", "api_key", "claim_url", "verification_code", "status",
    # DM responses
    "conversations", "conversation", "messages", "requests", "request",
    "has_activity", "unread_count",
    # Feed/search
    "feed", "results", "total", "page", "limit", "offset",
    # Submolt responses
    "submolt", "submolts",
    # Pagination
    "next_cursor", "has_more", "cursor",
    # Meta
    "data",
}

# Keywords that strongly suggest a verification challenge field
_CHALLENGE_KEYWORDS = {
    "challenge", "verify", "verification", "captcha", "puzzle",
    "action_needed", "action_required", "prove", "confirm",
    "test", "quiz", "answer", "respond", "task",
    "human_check", "bot_check", "ai_check", "reverse_captcha",
}

# Response headers that might carry challenge info
_CHALLENGE_HEADER_PREFIXES = (
    "x-moltbook-challenge",
    "x-moltbook-verify",
    "x-moltbook-action",
    "x-challenge",
    "x-verification",
)


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


def _detect_challenges(
    body: Dict[str, Any],
    headers: Dict[str, str],
    endpoint: str,
    method: str,
) -> Optional[Dict[str, Any]]:
    """
    Scan an API response for verification challenges.

    Returns a challenge dict if one is detected, None otherwise.
    Detection strategy (layered):
      1. Explicit challenge fields in response body
      2. Unknown top-level fields that contain challenge keywords
      3. Any unknown top-level fields (logged for analysis)
      4. Challenge-related response headers
    """
    challenge_signals: List[Dict[str, Any]] = []
    unknown_fields: Dict[str, Any] = {}

    # --- Body scan ---
    if isinstance(body, dict):
        for key, value in body.items():
            key_lower = key.lower().replace("-", "_").replace(" ", "_")

            # Direct challenge field match
            if any(kw in key_lower for kw in _CHALLENGE_KEYWORDS):
                challenge_signals.append({
                    "source": "body_field",
                    "field": key,
                    "value": value,
                })

            # Track unknown top-level fields
            elif key not in _KNOWN_TOPLEVEL_FIELDS:
                unknown_fields[key] = value

        # Check nested "meta", "data", or similar wrapper objects
        for wrapper_key in ("meta", "data", "_meta", "system", "platform"):
            wrapper = body.get(wrapper_key)
            if isinstance(wrapper, dict):
                for key, value in wrapper.items():
                    key_lower = key.lower().replace("-", "_").replace(" ", "_")
                    if any(kw in key_lower for kw in _CHALLENGE_KEYWORDS):
                        challenge_signals.append({
                            "source": f"body.{wrapper_key}",
                            "field": key,
                            "value": value,
                        })

    # --- Header scan ---
    for header_name, header_value in headers.items():
        if any(header_name.lower().startswith(p) for p in _CHALLENGE_HEADER_PREFIXES):
            challenge_signals.append({
                "source": "header",
                "field": header_name,
                "value": header_value,
            })

    # --- Build result ---
    if challenge_signals:
        challenge = {
            "detected": True,
            "signals": challenge_signals,
            "endpoint": endpoint,
            "method": method,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "raw_unknown_fields": unknown_fields,
        }
        _log_challenge(challenge)
        return challenge

    # Log unknown fields even if no challenge keywords matched — for future analysis
    if unknown_fields:
        logger.info(
            "Moltbook unknown fields in %s %s: %s",
            method, endpoint, list(unknown_fields.keys())
        )
        _log_challenge({
            "detected": False,
            "unknown_fields": unknown_fields,
            "endpoint": endpoint,
            "method": method,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return None


def _log_challenge(data: Dict[str, Any]) -> None:
    """Append challenge detection data to the JSONL log file."""
    try:
        os.makedirs(os.path.dirname(CHALLENGE_LOG_PATH), exist_ok=True)
        with open(CHALLENGE_LOG_PATH, "a") as f:
            f.write(json.dumps(data, default=str) + "\n")
    except Exception as e:
        logger.warning("Failed to write challenge log: %s", e)


def _format_challenge_alert(challenge: Dict[str, Any]) -> str:
    """Format a challenge detection into a prominent alert string."""
    lines = [
        "",
        "=" * 60,
        "!! VERIFICATION CHALLENGE DETECTED !!",
        "=" * 60,
        f"Endpoint: {challenge.get('method', '?')} {challenge.get('endpoint', '?')}",
        f"Time: {challenge.get('timestamp', '?')}",
        "",
        "Signals:",
    ]
    for signal in challenge.get("signals", []):
        lines.append(f"  [{signal['source']}] {signal['field']} = {json.dumps(signal['value'], default=str)}")

    if challenge.get("raw_unknown_fields"):
        lines.append("")
        lines.append("Additional unknown fields:")
        for k, v in challenge["raw_unknown_fields"].items():
            lines.append(f"  {k} = {json.dumps(v, default=str)[:500]}")

    lines.extend([
        "",
        "ACTION REQUIRED: Review the challenge above and respond using moltbook_respond_challenge.",
        "If unsure how to respond, the full challenge data has been logged to:",
        f"  {CHALLENGE_LOG_PATH}",
        "=" * 60,
        "",
    ])
    return "\n".join(lines)


async def _make_request(
    method: str,
    endpoint: str,
    params: Optional[Dict] = None,
    json_data: Optional[Dict] = None,
    timeout: float = 30.0,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    Make a request to the Moltbook API.

    Returns (response_body, challenge_or_none).
    The challenge dict is non-None if a verification challenge was detected.
    Error responses (4xx/5xx) are also scanned for challenges before raising.
    """
    url = f"{MOLTBOOK_BASE_URL}{endpoint}"
    headers = _get_headers()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=timeout,
        )

        # Parse body
        try:
            body = response.json()
        except Exception:
            body = {"_raw_text": response.text}

        # Extract response headers as plain dict
        resp_headers = dict(response.headers)

        # Always scan for challenges — even on error responses
        challenge = _detect_challenges(body, resp_headers, endpoint, method)

        if response.status_code >= 400:
            # On error, still return the challenge if one was detected
            error_text = response.text
            if challenge:
                raise Exception(
                    f"Moltbook API error ({response.status_code}): {error_text}\n\n"
                    + _format_challenge_alert(challenge)
                )
            raise Exception(f"Moltbook API error ({response.status_code}): {error_text}")

        return body, challenge


def _append_challenge_to_output(output_text: str, challenge: Optional[Dict[str, Any]]) -> str:
    """If a challenge was detected, append the alert to the tool output."""
    if challenge:
        return output_text + "\n" + _format_challenge_alert(challenge)
    return output_text


# =============================================================================
# Existing tools — updated to use new _make_request signature
# =============================================================================

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

Returns a list of posts with id, title, content/url, author, votes, comment count, and submolt.
Also scans every response for verification challenges.""",
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

        if submolt:
            params["submolt"] = submolt

        endpoint = "/posts"
        data, challenge = await _make_request("GET", endpoint, params=params)

        posts = data.get("posts", data) if isinstance(data, dict) else data

        if not posts:
            text = f"No posts found (sort: {sort}, submolt: {submolt or 'all'})"
            return {"content": [{"type": "text", "text": _append_challenge_to_output(text, challenge)}]}

        output = [f"## Moltbook Feed ({sort})\n"]
        if submolt:
            output.append(f"**Submolt:** m/{submolt}\n")
        output.append(f"**{len(posts)} posts**\n")

        for post in posts:
            post_id = post.get("id", "unknown")
            title = post.get("title", "Untitled")
            author_data = post.get("author", {})
            author = author_data.get("name", "unknown") if isinstance(author_data, dict) else str(author_data)
            upvotes = post.get("upvotes", 0)
            downvotes = post.get("downvotes", 0)
            votes = upvotes - downvotes
            comments = post.get("comment_count", post.get("num_comments", 0))
            submolt_data = post.get("submolt", {})
            post_submolt = submolt_data.get("name", "general") if isinstance(submolt_data, dict) else str(submolt_data)
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

        text = "\n".join(output)
        return {"content": [{"type": "text", "text": _append_challenge_to_output(text, challenge)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_post",
    description="""Create a new post on Moltbook.

Create a text post or link post in a submolt (community). Text posts have a body, link posts have a URL.

Common submolts: general, aithoughts, tech, science, programming

Also scans the response for verification challenges.""",
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

        result, challenge = await _make_request("POST", "/posts", json_data=post_data)

        post = result.get("post", result)
        post_id = post.get("id", post.get("post_id", "unknown"))
        post_url = post.get("url", f"https://www.moltbook.com/post/{post_id}")

        text = f"Post created successfully!\n\n**ID:** `{post_id}`\n**Title:** {title}\n**Submolt:** m/{submolt}\n**URL:** {post_url}"
        return {"content": [{"type": "text", "text": _append_challenge_to_output(text, challenge)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_comment",
    description="""Comment on a Moltbook post or reply to a comment.

Add a comment to a post, or reply to an existing comment by providing the parent_comment_id.

Also scans the response for verification challenges.""",
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

        result, challenge = await _make_request("POST", f"/posts/{post_id}/comments", json_data=comment_data)

        comment = result.get("comment", result)
        comment_id = comment.get("id", comment.get("comment_id", "unknown"))

        reply_note = f" (reply to `{parent_id}`)" if parent_id else ""
        text = f"Comment posted{reply_note}!\n\n**Comment ID:** `{comment_id}`\n**Post ID:** `{post_id}`\n**Content:** {content[:200]}{'...' if len(content) > 200 else ''}"
        return {"content": [{"type": "text", "text": _append_challenge_to_output(text, challenge)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_get_post",
    description="""Get a specific Moltbook post with its comments.

Retrieves full post details and threaded comments. Use this after browsing the feed to read a post in detail.

Also scans every response for verification challenges.""",
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

        data, challenge = await _make_request("GET", f"/posts/{post_id}")
        post = data.get("post", data)

        comments = data.get("comments", [])
        if comment_sort != "top" or not comments:
            try:
                comments_data, challenge2 = await _make_request(
                    "GET",
                    f"/posts/{post_id}/comments",
                    params={"sort": comment_sort}
                )
                comments = comments_data.get("comments", comments_data) if isinstance(comments_data, dict) else comments_data
                # Use whichever challenge was detected
                challenge = challenge or challenge2
            except Exception:
                pass

        # Format output
        title = post.get("title", "Untitled")
        author_data = post.get("author", {})
        author = author_data.get("name", "unknown") if isinstance(author_data, dict) else str(author_data)
        upvotes = post.get("upvotes", 0)
        downvotes = post.get("downvotes", 0)
        votes = upvotes - downvotes
        comment_count = post.get("comment_count", post.get("num_comments", 0))
        submolt_data = post.get("submolt", {})
        submolt = submolt_data.get("name", "general") if isinstance(submolt_data, dict) else str(submolt_data)
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
            indent = "  " * depth
            author_data = comment.get("author", {})
            author = author_data.get("name", "unknown") if isinstance(author_data, dict) else str(author_data)
            text = comment.get("content", comment.get("body", ""))
            upvotes = comment.get("upvotes", 0)
            downvotes = comment.get("downvotes", 0)
            votes = upvotes - downvotes
            cid = comment.get("id", "")

            lines = [f"{indent}**{author}** ({votes} points) `{cid}`"]
            lines.append(f"{indent}{text}")

            replies = comment.get("replies", comment.get("children", []))
            if replies:
                for reply in replies:
                    lines.append(format_comment(reply, depth + 1))

            return "\n".join(lines)

        if comments:
            for comment in comments[:50]:
                output.append(format_comment(comment))
                output.append("")
        else:
            output.append("*No comments yet*")

        text = "\n".join(output)
        return {"content": [{"type": "text", "text": _append_challenge_to_output(text, challenge)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


# =============================================================================
# New tools
# =============================================================================

@register_tool("moltbook")
@tool(
    name="moltbook_account_status",
    description="""Check your Moltbook account status.

Returns profile info, suspension state, karma, and any pending verification challenges.
Use this proactively before posting/commenting to check if your account is in good standing.

Also scans the response for verification challenges — this is the most likely endpoint
to carry challenge data since it's about your account state.""",
    input_schema={
        "type": "object",
        "properties": {}
    }
)
async def moltbook_account_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check account status and look for challenges."""
    try:
        # Check both /agents/me and /agents/status for maximum coverage
        results = []
        all_challenges = []

        # Primary profile endpoint
        try:
            me_data, challenge = await _make_request("GET", "/agents/me")
            results.append(("profile", me_data))
            if challenge:
                all_challenges.append(challenge)
        except Exception as e:
            results.append(("profile_error", str(e)))

        # Status endpoint (may have different fields)
        try:
            status_data, challenge = await _make_request("GET", "/agents/status")
            results.append(("status", status_data))
            if challenge:
                all_challenges.append(challenge)
        except Exception as e:
            results.append(("status_error", str(e)))

        # Format output
        output = ["## Moltbook Account Status\n"]

        for label, data in results:
            if label == "profile" and isinstance(data, dict):
                agent = data.get("agent", data)
                output.append(f"**Name:** {agent.get('name', '?')}")
                output.append(f"**Display Name:** {agent.get('display_name', '?')}")
                output.append(f"**Status:** {agent.get('status', '?')}")
                output.append(f"**Karma:** {agent.get('karma', '?')}")
                output.append(f"**Followers:** {agent.get('follower_count', '?')}")
                output.append(f"**Following:** {agent.get('following_count', '?')}")
                output.append(f"**Claimed:** {agent.get('is_claimed', '?')}")
                output.append("")

                # Dump ALL fields for inspection
                known_agent_fields = {
                    "id", "name", "display_name", "description", "avatar_url",
                    "karma", "status", "is_claimed", "follower_count",
                    "following_count", "created_at", "owner",
                }
                extra = {k: v for k, v in agent.items() if k not in known_agent_fields}
                if extra:
                    output.append("**Additional fields (may contain challenge data):**")
                    for k, v in extra.items():
                        output.append(f"  `{k}`: {json.dumps(v, default=str)[:500]}")
                    output.append("")

            elif label == "status" and isinstance(data, dict):
                output.append(f"**Claim Status:** {data.get('status', json.dumps(data, default=str))}")

                # Dump all fields
                extra = {k: v for k, v in data.items() if k not in {"status", "success"}}
                if extra:
                    output.append("**Additional status fields:**")
                    for k, v in extra.items():
                        output.append(f"  `{k}`: {json.dumps(v, default=str)[:500]}")
                output.append("")

            elif "error" in label:
                output.append(f"**{label}:** {data}")
                output.append("")

        # Full raw response dump for analysis
        output.append("---\n**Raw responses (for challenge analysis):**")
        for label, data in results:
            output.append(f"\n`{label}`: ```json\n{json.dumps(data, indent=2, default=str)[:2000]}\n```")

        text = "\n".join(output)

        # Append any challenge alerts
        for ch in all_challenges:
            text = _append_challenge_to_output(text, ch)

        return {"content": [{"type": "text", "text": text}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_check_dms",
    description="""Check Moltbook direct messages for verification challenges.

Checks for DM activity and pending DM requests. Verification challenges may arrive
as system DMs. This tool:
1. Polls /agents/dm/check for any DM activity
2. Checks /agents/dm/requests for pending conversation requests
3. Lists recent conversations
4. Scans ALL responses for challenge fields

Use this proactively — challenges that arrive as DMs need to be answered.""",
    input_schema={
        "type": "object",
        "properties": {
            "include_conversations": {
                "type": "boolean",
                "description": "Also list recent conversations (default: true)",
                "default": True
            }
        }
    }
)
async def moltbook_check_dms(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check DMs for verification challenges."""
    try:
        include_convos = args.get("include_conversations", True)
        output = ["## Moltbook DM Check\n"]
        all_challenges = []

        # 1. Quick activity check
        try:
            activity, challenge = await _make_request("GET", "/agents/dm/check")
            if challenge:
                all_challenges.append(challenge)

            has_activity = activity.get("has_activity", False)
            unread = activity.get("unread_count", 0)
            output.append(f"**Activity:** {'Yes' if has_activity else 'None'}")
            output.append(f"**Unread:** {unread}")

            # Dump full response
            extra = {k: v for k, v in activity.items()
                     if k not in {"has_activity", "unread_count", "success"}}
            if extra:
                output.append("**Additional fields:**")
                for k, v in extra.items():
                    output.append(f"  `{k}`: {json.dumps(v, default=str)[:500]}")
            output.append("")
        except Exception as e:
            output.append(f"**DM check failed:** {e}\n")

        # 2. Pending requests (challenges may arrive as DM requests)
        try:
            requests_data, challenge = await _make_request("GET", "/agents/dm/requests")
            if challenge:
                all_challenges.append(challenge)

            requests = requests_data.get("requests", [])
            if requests:
                output.append(f"**Pending DM Requests ({len(requests)}):**")
                for req in requests:
                    sender = req.get("from", req.get("sender", req.get("agent", {})))
                    sender_name = sender.get("name", str(sender)) if isinstance(sender, dict) else str(sender)
                    msg = req.get("message", req.get("content", ""))
                    req_id = req.get("id", "?")
                    output.append(f"  - From **{sender_name}** (ID: `{req_id}`): {msg[:300]}")
                output.append("")
            else:
                output.append("**Pending DM Requests:** None\n")

            # Dump raw for analysis
            extra = {k: v for k, v in requests_data.items()
                     if k not in {"requests", "success"}}
            if extra:
                output.append("**Additional request fields:**")
                for k, v in extra.items():
                    output.append(f"  `{k}`: {json.dumps(v, default=str)[:500]}")
                output.append("")
        except Exception as e:
            output.append(f"**DM requests check failed:** {e}\n")

        # 3. Recent conversations
        if include_convos:
            try:
                convos_data, challenge = await _make_request("GET", "/agents/dm/conversations")
                if challenge:
                    all_challenges.append(challenge)

                convos = convos_data.get("conversations", [])
                if convos:
                    output.append(f"**Recent Conversations ({len(convos)}):**")
                    for convo in convos[:10]:
                        other = convo.get("other_agent", convo.get("agent", {}))
                        other_name = other.get("name", str(other)) if isinstance(other, dict) else str(other)
                        convo_id = convo.get("id", "?")
                        last_msg = convo.get("last_message", {})
                        last_text = last_msg.get("content", "") if isinstance(last_msg, dict) else str(last_msg)
                        unread = convo.get("unread", convo.get("unread_count", 0))
                        output.append(f"  - **{other_name}** (ID: `{convo_id}`){' [UNREAD]' if unread else ''}: {last_text[:200]}")
                    output.append("")
                else:
                    output.append("**Conversations:** None\n")
            except Exception as e:
                output.append(f"**Conversations check failed:** {e}\n")

        text = "\n".join(output)
        for ch in all_challenges:
            text = _append_challenge_to_output(text, ch)

        return {"content": [{"type": "text", "text": text}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_respond_challenge",
    description="""Respond to a Moltbook verification challenge.

When a verification challenge is detected (via any Moltbook tool), use this to submit
your response. Since the challenge system is undocumented, this tool tries multiple
response strategies:

1. POST to the challenge URL if one was provided in the challenge data
2. POST to common challenge endpoints (/agents/me/challenge, /agents/me/verify, etc.)
3. Reply via DM if the challenge came from a DM

Provide the challenge data from the detection alert and your answer.""",
    input_schema={
        "type": "object",
        "properties": {
            "answer": {
                "type": "string",
                "description": "Your answer/response to the verification challenge"
            },
            "challenge_id": {
                "type": "string",
                "description": "Challenge ID if one was provided in the detection alert (optional)"
            },
            "challenge_url": {
                "type": "string",
                "description": "Challenge URL/endpoint if one was provided (optional)"
            },
            "dm_conversation_id": {
                "type": "string",
                "description": "DM conversation ID if the challenge arrived via DM (optional)"
            }
        },
        "required": ["answer"]
    }
)
async def moltbook_respond_challenge(args: Dict[str, Any]) -> Dict[str, Any]:
    """Respond to a verification challenge."""
    try:
        answer = args.get("answer", "").strip()
        challenge_id = args.get("challenge_id", "").strip()
        challenge_url = args.get("challenge_url", "").strip()
        dm_convo_id = args.get("dm_conversation_id", "").strip()

        if not answer:
            return {"content": [{"type": "text", "text": "Error: answer is required"}], "is_error": True}

        output = ["## Challenge Response Attempts\n"]
        success = False

        # Strategy 1: If a specific URL was provided
        if challenge_url:
            try:
                # Handle both full URLs and relative endpoints
                if challenge_url.startswith("http"):
                    # Full URL — make request directly
                    headers = _get_headers()
                    async with httpx.AsyncClient(follow_redirects=True) as client:
                        response = await client.post(
                            challenge_url,
                            headers=headers,
                            json={"answer": answer, "challenge_id": challenge_id} if challenge_id else {"answer": answer},
                            timeout=30.0,
                        )
                        body = response.json() if response.status_code < 500 else {"_raw": response.text}
                        output.append(f"**Custom URL ({challenge_url}):** {response.status_code}")
                        output.append(f"  Response: {json.dumps(body, default=str)[:500]}")
                        if response.status_code < 400:
                            success = True
                else:
                    # Relative endpoint
                    payload = {"answer": answer}
                    if challenge_id:
                        payload["challenge_id"] = challenge_id
                    result, ch = await _make_request("POST", challenge_url, json_data=payload)
                    output.append(f"**{challenge_url}:** {json.dumps(result, default=str)[:500]}")
                    success = result.get("success", False)
                output.append("")
            except Exception as e:
                output.append(f"**Custom URL failed:** {e}\n")

        # Strategy 2: Try common challenge endpoints
        challenge_endpoints = [
            "/agents/me/challenge",
            "/agents/me/verify",
            "/agents/me/verification",
            "/agents/challenge",
            "/agents/verify",
            "/verification/respond",
            "/challenge/respond",
        ]

        payload = {"answer": answer, "response": answer}
        if challenge_id:
            payload["challenge_id"] = challenge_id
            payload["id"] = challenge_id

        for ep in challenge_endpoints:
            if success:
                break
            try:
                result, ch = await _make_request("POST", ep, json_data=payload)
                output.append(f"**{ep}:** {json.dumps(result, default=str)[:500]}")
                if result.get("success"):
                    success = True
                    output.append("  ^ SUCCESS!")
                output.append("")
            except Exception as e:
                error_str = str(e)
                if "404" in error_str:
                    # Expected — endpoint doesn't exist, try next
                    continue
                elif "405" in error_str:
                    # Method not allowed — try GET with query params
                    try:
                        result, ch = await _make_request("GET", ep, params={"answer": answer})
                        output.append(f"**{ep} (GET):** {json.dumps(result, default=str)[:500]}")
                        if result.get("success"):
                            success = True
                        output.append("")
                    except Exception:
                        continue
                else:
                    output.append(f"**{ep}:** {error_str[:200]}\n")

        # Strategy 3: Reply via DM if conversation ID provided
        if dm_convo_id and not success:
            try:
                result, ch = await _make_request(
                    "POST",
                    f"/agents/dm/conversations/{dm_convo_id}/send",
                    json_data={"content": answer}
                )
                output.append(f"**DM reply to {dm_convo_id}:** {json.dumps(result, default=str)[:500]}")
                if result.get("success"):
                    success = True
                    output.append("  ^ DM sent successfully")
                output.append("")
            except Exception as e:
                output.append(f"**DM reply failed:** {e}\n")

        # Summary
        output.append("---")
        if success:
            output.append("**Result: At least one response endpoint accepted the answer.**")
            output.append("Check account status with `moltbook_account_status` to verify.")
        else:
            output.append("**Result: No known endpoint accepted the challenge response.**")
            output.append("The challenge response mechanism may use an endpoint we haven't discovered.")
            output.append("Consider:")
            output.append("- Checking DMs for system messages with reply instructions")
            output.append("- Looking at the challenge log for clues: " + CHALLENGE_LOG_PATH)
            output.append("- Contacting Moltbook support")

        # Log the attempt
        _log_challenge({
            "type": "response_attempt",
            "answer": answer[:200],
            "challenge_id": challenge_id,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_challenge_log",
    description="""View the Moltbook verification challenge detection log.

Shows all detected challenges, unknown fields, and response attempts from the
persistent JSONL log. Use this to analyze patterns in how challenges are delivered.""",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of recent entries to show (default: 20)",
                "default": 20
            }
        }
    }
)
async def moltbook_challenge_log(args: Dict[str, Any]) -> Dict[str, Any]:
    """View the challenge detection log."""
    try:
        limit = args.get("limit", 20)

        if not os.path.exists(CHALLENGE_LOG_PATH):
            return {"content": [{"type": "text", "text": "## Moltbook Challenge Log\n\nNo log entries yet. Challenges will be logged when detected."}]}

        entries = []
        with open(CHALLENGE_LOG_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if not entries:
            return {"content": [{"type": "text", "text": "## Moltbook Challenge Log\n\nLog file exists but contains no valid entries."}]}

        # Show most recent entries
        recent = entries[-limit:]
        output = [f"## Moltbook Challenge Log ({len(recent)} of {len(entries)} entries)\n"]

        for entry in recent:
            detected = entry.get("detected")
            entry_type = entry.get("type", "detection")
            ts = entry.get("timestamp", "?")

            if entry_type == "response_attempt":
                output.append(f"**[{ts}] RESPONSE ATTEMPT**")
                output.append(f"  Answer: {entry.get('answer', '?')[:200]}")
                output.append(f"  Success: {entry.get('success', '?')}")
            elif detected:
                output.append(f"**[{ts}] CHALLENGE DETECTED**")
                output.append(f"  Endpoint: {entry.get('method', '?')} {entry.get('endpoint', '?')}")
                for signal in entry.get("signals", []):
                    output.append(f"  Signal [{signal['source']}]: {signal['field']} = {json.dumps(signal['value'], default=str)[:300]}")
            else:
                output.append(f"**[{ts}] Unknown fields**")
                output.append(f"  Endpoint: {entry.get('method', '?')} {entry.get('endpoint', '?')}")
                for k, v in entry.get("unknown_fields", {}).items():
                    output.append(f"  `{k}`: {json.dumps(v, default=str)[:300]}")

            output.append("")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("moltbook")
@tool(
    name="moltbook_notifications",
    description="""Check Moltbook notifications and replies.

Checks DM activity, pending DM requests, and account status for any notifications.
Also scans all responses for verification challenges.""",
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
    """Check notifications — now uses DM check as proxy."""
    try:
        output = ["## Moltbook Notifications\n"]
        output.append("*Note: Moltbook has no dedicated notifications endpoint. Checking DMs and account status instead.*\n")
        all_challenges = []

        # Check DM activity
        try:
            activity, challenge = await _make_request("GET", "/agents/dm/check")
            if challenge:
                all_challenges.append(challenge)
            has_activity = activity.get("has_activity", False)
            unread = activity.get("unread_count", 0)
            if has_activity or unread:
                output.append(f"**DM Activity:** {unread} unread message(s)")
                output.append("Use `moltbook_check_dms` for details.\n")
            else:
                output.append("**DMs:** No new activity\n")
        except Exception as e:
            output.append(f"**DM check:** {e}\n")

        # Check pending DM requests
        try:
            requests_data, challenge = await _make_request("GET", "/agents/dm/requests")
            if challenge:
                all_challenges.append(challenge)
            requests = requests_data.get("requests", [])
            if requests:
                output.append(f"**Pending DM Requests:** {len(requests)}")
                for req in requests[:5]:
                    sender = req.get("from", req.get("sender", req.get("agent", {})))
                    sender_name = sender.get("name", str(sender)) if isinstance(sender, dict) else str(sender)
                    output.append(f"  - From **{sender_name}**")
                output.append("")
        except Exception:
            pass

        # Suggestion
        output.append("**To check for replies:** Use `moltbook_get_post` with your post IDs.")
        output.append("**To check account health:** Use `moltbook_account_status`.")

        text = "\n".join(output)
        for ch in all_challenges:
            text = _append_challenge_to_output(text, ch)

        return {"content": [{"type": "text", "text": text}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
