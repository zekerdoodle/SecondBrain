"""
YouTube Music tools.

Tools for managing YouTube Music playlists and songs.
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


@register_tool("youtube")
@tool(
    name="ytmusic_get_playlists",
    description="Get user's YouTube Music playlists.",
    input_schema={
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "Maximum playlists to return (default 25)",
                "default": 25
            }
        }
    }
)
async def ytmusic_get_playlists(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get YouTube Music playlists."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        playlists = youtube_tools.get_playlists(service, args.get("max_results", 25))

        return {"content": [{"type": "text", "text": youtube_tools.format_playlist_list(playlists)}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_get_playlist_items",
    description="Get songs in a specific playlist.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "Playlist ID"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum items to return (default 50)",
                "default": 50
            }
        },
        "required": ["playlist_id"]
    }
)
async def ytmusic_get_playlist_items(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get items in a YouTube Music playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        items = youtube_tools.get_playlist_items(service, args["playlist_id"], args.get("max_results", 50))

        return {"content": [{"type": "text", "text": youtube_tools.format_track_list(items)}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_get_liked",
    description="Get user's liked songs on YouTube Music.",
    input_schema={
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "Maximum songs to return (default 50)",
                "default": 50
            }
        }
    }
)
async def ytmusic_get_liked(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get liked songs."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        items = youtube_tools.get_liked_music(service, args.get("max_results", 50))

        return {"content": [{"type": "text", "text": youtube_tools.format_track_list(items, "Liked Songs")}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_search",
    description="Search for music on YouTube Music.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (song name, artist, etc.)"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default 10)",
                "default": 10
            }
        },
        "required": ["query"]
    }
)
async def ytmusic_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search YouTube Music."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        results = youtube_tools.search_music(service, args["query"], args.get("max_results", 10))

        return {"content": [{"type": "text", "text": youtube_tools.format_search_results(results)}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_create_playlist",
    description="Create a new YouTube Music playlist.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Playlist title"
            },
            "description": {
                "type": "string",
                "description": "Playlist description (optional)",
                "default": ""
            },
            "privacy": {
                "type": "string",
                "description": "Privacy: 'private', 'public', or 'unlisted' (default: private)",
                "default": "private"
            }
        },
        "required": ["title"]
    }
)
async def ytmusic_create_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a YouTube Music playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.create_playlist(
            service,
            args["title"],
            args.get("description", ""),
            args.get("privacy", "private")
        )

        return {"content": [{"type": "text", "text": f"Playlist created!\nTitle: {result['title']}\nID: `{result['id']}`\nURL: {result['url']}"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_add_to_playlist",
    description="Add a song to a playlist. Use ytmusic_search to find video_id first.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "Playlist ID to add to"
            },
            "video_id": {
                "type": "string",
                "description": "Video ID of the song to add"
            }
        },
        "required": ["playlist_id", "video_id"]
    }
)
async def ytmusic_add_to_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a song to a playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.add_to_playlist(service, args["playlist_id"], args["video_id"])

        return {"content": [{"type": "text", "text": "Song added to playlist!"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_remove_from_playlist",
    description="Remove a song from a playlist. Requires the playlist_item_id (not video_id) which you can get from ytmusic_get_playlist_items.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_item_id": {
                "type": "string",
                "description": "The playlist item ID (from ytmusic_get_playlist_items, not the video_id)"
            }
        },
        "required": ["playlist_item_id"]
    }
)
async def ytmusic_remove_from_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a song from a playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.remove_from_playlist(service, args["playlist_item_id"])

        return {"content": [{"type": "text", "text": "Song removed from playlist!"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("youtube")
@tool(
    name="ytmusic_delete_playlist",
    description="Delete a playlist permanently. Use with caution - this cannot be undone.",
    input_schema={
        "type": "object",
        "properties": {
            "playlist_id": {
                "type": "string",
                "description": "ID of the playlist to delete"
            }
        },
        "required": ["playlist_id"]
    }
)
async def ytmusic_delete_playlist(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a playlist."""
    try:
        import google_tools
        import youtube_tools

        creds = google_tools.authenticate()
        service = youtube_tools.get_youtube_service(creds)
        result = youtube_tools.delete_playlist(service, args["playlist_id"])

        return {"content": [{"type": "text", "text": "Playlist deleted!"}]}
    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
