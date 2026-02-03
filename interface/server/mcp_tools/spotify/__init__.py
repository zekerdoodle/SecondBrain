"""Spotify tools."""

# Import to trigger registration
from . import playback

# Re-export for direct access
from .playback import (
    spotify_auth_start,
    spotify_auth_callback,
    spotify_recently_played,
    spotify_top_items,
    spotify_search,
    spotify_get_playlists,
    spotify_create_playlist,
    spotify_add_to_playlist,
    spotify_now_playing,
    spotify_playback_control,
)

__all__ = [
    "spotify_auth_start",
    "spotify_auth_callback",
    "spotify_recently_played",
    "spotify_top_items",
    "spotify_search",
    "spotify_get_playlists",
    "spotify_create_playlist",
    "spotify_add_to_playlist",
    "spotify_now_playing",
    "spotify_playback_control",
]
