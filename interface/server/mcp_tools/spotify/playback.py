"""
Spotify tools.

Tools for managing Spotify playlists, playback, and music discovery.
"""

import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@register_tool("spotify")
@tool(
    name="spotify_auth_start",
    description="""Start Spotify OAuth flow. Returns an authorization URL.

Visit the URL in a browser, authorize the app, then copy the 'code' parameter
from the redirect URL and pass it to spotify_auth_callback.

Environment variables must be set:
- SPOTIFY_CLIENT_ID
- SPOTIFY_CLIENT_SECRET
- SPOTIFY_REDIRECT_URI (optional, defaults to http://localhost:8888/callback)""",
    input_schema={"type": "object", "properties": {}}
)
async def spotify_auth_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Start Spotify OAuth flow."""
    try:
        import spotify_tools

        result = spotify_tools.auth_start()
        output = f"""## Spotify Authorization

1. Visit this URL in your browser:
   {result['auth_url']}

2. Log in and authorize the app

3. You'll be redirected to: {result['redirect_uri']}?code=XXX...

4. Copy the 'code' parameter value and call:
   `spotify_auth_callback` with the code

State (for verification): {result['state']}
"""
        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_auth_callback",
    description="""Complete Spotify OAuth by exchanging the authorization code for tokens.

After visiting the auth URL from spotify_auth_start and authorizing, you'll be
redirected to a URL like: http://localhost:8888/callback?code=ABC123...

Extract the code parameter value and pass it here.""",
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Authorization code from the redirect URL"}
        },
        "required": ["code"]
    }
)
async def spotify_auth_callback(args: Dict[str, Any]) -> Dict[str, Any]:
    """Exchange auth code for tokens."""
    try:
        import spotify_tools

        code = args.get("code", "").strip()
        if not code:
            return {"content": [{"type": "text", "text": "Error: code is required"}], "is_error": True}

        result = spotify_tools.auth_callback(code)
        return {"content": [{"type": "text", "text": f"Successfully authenticated with Spotify! Token expires in {result.get('expires_in', 3600)} seconds."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_recently_played",
    description="Get the user's recently played tracks on Spotify.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of tracks to return (default: 20, max: 50)", "default": 20}
        }
    }
)
async def spotify_recently_played(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get recently played tracks."""
    try:
        import spotify_tools

        limit = args.get("limit", 20)
        tracks = spotify_tools.get_recently_played(limit=limit)
        output = spotify_tools.format_recently_played(tracks)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_top_items",
    description="""Get the user's top artists or tracks.

time_range options:
- short_term: Last 4 weeks
- medium_term: Last 6 months (default)
- long_term: All time""",
    input_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["artists", "tracks"],
                "description": "Type of items to get",
                "default": "tracks"
            },
            "time_range": {
                "type": "string",
                "enum": ["short_term", "medium_term", "long_term"],
                "description": "Time range for top items",
                "default": "medium_term"
            },
            "limit": {"type": "integer", "description": "Number of items (default: 20, max: 50)", "default": 20}
        }
    }
)
async def spotify_top_items(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get top artists or tracks."""
    try:
        import spotify_tools

        item_type = args.get("type", "tracks")
        time_range = args.get("time_range", "medium_term")
        limit = args.get("limit", 20)

        items = spotify_tools.get_top_items(item_type=item_type, time_range=time_range, limit=limit)
        output = spotify_tools.format_top_items(items, item_type)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_search",
    description="""Search Spotify for tracks, artists, albums, or playlists.

Returns matching items with their Spotify URIs (needed for adding to playlists or playback).""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "type": {
                "type": "string",
                "enum": ["track", "artist", "album", "playlist"],
                "description": "Type of items to search for",
                "default": "track"
            },
            "limit": {"type": "integer", "description": "Number of results (default: 10, max: 50)", "default": 10}
        },
        "required": ["query"]
    }
)
async def spotify_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search Spotify."""
    try:
        import spotify_tools

        query = args.get("query", "").strip()
        if not query:
            return {"content": [{"type": "text", "text": "Error: query is required"}], "is_error": True}

        search_type = args.get("type", "track")
        limit = args.get("limit", 10)

        items = spotify_tools.search(query=query, search_type=search_type, limit=limit)
        output = spotify_tools.format_search_results(items, search_type)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_get_playlists",
    description="Get the user's Spotify playlists.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of playlists (default: 20, max: 50)", "default": 20}
        }
    }
)
async def spotify_get_playlists(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get user's playlists."""
    try:
        import spotify_tools

        limit = args.get("limit", 20)
        playlists = spotify_tools.get_playlists(limit=limit)
        output = spotify_tools.format_playlists(playlists)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_create_playlist",
    description="Create a new Spotify playlist.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Playlist name"},
            "description": {"type": "string", "description": "Playlist description", "default": ""},
            "public": {"type": "boolean", "description": "Make playlist public (default: true)", "default": True}
        },
        "required": ["name"]
    }
)
async def spotify_create_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new playlist."""
    try:
        import spotify_tools

        name = args.get("name", "").strip()
        if not name:
            return {"content": [{"type": "text", "text": "Error: name is required"}], "is_error": True}

        description = args.get("description", "")
        public = args.get("public", True)

        result = spotify_tools.create_playlist(name=name, description=description, public=public)

        output = f"""## Playlist Created

**{result.get('name')}**
ID: `{result.get('id')}`
URL: {result.get('spotify_url')}

Use `spotify_add_to_playlist` with this ID to add tracks."""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_add_to_playlist",
    description="""Add tracks to a Spotify playlist.

Use spotify_search to find track URIs first. Track URIs look like: spotify:track:4iV5W9uYEdYUVa79Axb7Rh""",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {"type": "string", "description": "Playlist ID (from spotify_get_playlists or spotify_create_playlist)"},
            "track_uris": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of Spotify track URIs to add"
            }
        },
        "required": ["playlist_id", "track_uris"]
    }
)
async def spotify_add_to_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add tracks to a playlist."""
    try:
        import spotify_tools

        playlist_id = args.get("playlist_id", "").strip()
        track_uris = args.get("track_uris", [])

        if not playlist_id:
            return {"content": [{"type": "text", "text": "Error: playlist_id is required"}], "is_error": True}
        if not track_uris:
            return {"content": [{"type": "text", "text": "Error: track_uris is required"}], "is_error": True}

        result = spotify_tools.add_to_playlist(playlist_id=playlist_id, track_uris=track_uris)

        return {"content": [{"type": "text", "text": f"Added {result.get('tracks_added')} tracks to playlist."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_now_playing",
    description="Get the currently playing track on Spotify.",
    input_schema={"type": "object", "properties": {}}
)
async def spotify_now_playing(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get currently playing track."""
    try:
        import spotify_tools

        data = spotify_tools.get_now_playing()
        output = spotify_tools.format_now_playing(data)

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("spotify")
@tool(
    name="spotify_playback_control",
    description="""Control Spotify playback.

**Requires Spotify Premium.**

Actions:
- play: Resume playback
- pause: Pause playback
- next: Skip to next track
- previous: Go to previous track""",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "next", "previous"],
                "description": "Playback action"
            }
        },
        "required": ["action"]
    }
)
async def spotify_playback_control(args: Dict[str, Any]) -> Dict[str, Any]:
    """Control playback."""
    try:
        import spotify_tools

        action = args.get("action", "")
        if not action:
            return {"content": [{"type": "text", "text": "Error: action is required"}], "is_error": True}

        result = spotify_tools.playback_control(action=action)

        action_msgs = {
            "play": "Playback resumed",
            "pause": "Playback paused",
            "next": "Skipped to next track",
            "previous": "Went to previous track",
        }

        return {"content": [{"type": "text", "text": action_msgs.get(action, f"Action '{action}' completed")}]}

    except Exception as e:
        error_msg = str(e)
        if "PREMIUM_REQUIRED" in error_msg or "403" in error_msg:
            error_msg = "This feature requires Spotify Premium."
        return {"content": [{"type": "text", "text": f"Error: {error_msg}"}], "is_error": True}
