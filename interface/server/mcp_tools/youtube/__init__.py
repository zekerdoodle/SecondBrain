"""YouTube Music tools."""

# Import to trigger registration
from . import music

# Re-export for direct access
from .music import (
    ytmusic_get_playlists,
    ytmusic_get_playlist_items,
    ytmusic_get_liked,
    ytmusic_search,
    ytmusic_create_playlist,
    ytmusic_add_to_playlist,
    ytmusic_remove_from_playlist,
    ytmusic_delete_playlist,
)

__all__ = [
    "ytmusic_get_playlists",
    "ytmusic_get_playlist_items",
    "ytmusic_get_liked",
    "ytmusic_search",
    "ytmusic_create_playlist",
    "ytmusic_add_to_playlist",
    "ytmusic_remove_from_playlist",
    "ytmusic_delete_playlist",
]
