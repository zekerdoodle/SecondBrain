# YouTube Music API "Now Playing" Investigation

**Date:** 2026-02-03
**Question:** Can we get the currently playing track from YouTube Music API (ytmusicapi)?

## Summary

**No, there is no "now playing" endpoint in YouTube Music API.**

The YouTube Music ecosystem (both ytmusicapi library and Google's official YouTube Data API v3) does **not** provide real-time playback state or "currently playing" information. This is a fundamental API limitation, not something we can work around.

---

## Detailed Findings

### 1. ytmusicapi Library Analysis

**Source:** https://github.com/sigma67/ytmusicapi

The `ytmusicapi` Python library provides comprehensive YouTube Music functionality through these mixins:
- `BrowsingMixin` - Artist/album/song browsing
- `SearchMixin` - Search functionality
- `WatchMixin` - Watch playlists (next songs in queue)
- `ChartsMixin` - Chart data
- `ExploreMixin` - Discovery features
- `LibraryMixin` - Library management, **including history**
- `PlaylistsMixin` - Playlist operations
- `PodcastsMixin` - Podcast functionality
- `UploadsMixin` - Upload management

**Available playback-related methods:**
- `get_history()` - Gets play history in reverse chronological order
  - Returns list of previously played tracks with `played` timestamps
  - Can be used to see what was *recently* played, but NOT what is *currently* playing
- `get_watch_playlist()` - Gets suggested next songs (queue/radio)

**Missing methods:**
- ❌ No `get_now_playing()` or equivalent
- ❌ No `get_playback_state()`
- ❌ No `get_current_track()`
- ❌ No player control methods (play, pause, skip)

### 2. YouTube Data API v3

**Source:** https://developers.google.com/youtube/v3/docs

The official Google YouTube Data API v3 focuses on content management and metadata, not real-time playback.

**Available resources:**
- Videos, Channels, Playlists, PlaylistItems
- Comments, Subscriptions, Activities
- Captions, Thumbnails, Search

**Missing:**
- ❌ No player state endpoints
- ❌ No currently playing track endpoints
- ❌ No playback control endpoints

**Note:** The YouTube Player API (IFrame Player API) provides *embedded player* control for web players, but does not expose user account playback state.

### 3. Our Current Implementation

**Location:** `/home/debian/second_brain/interface/server/mcp_tools/youtube/music.py`

**Current YouTube Music tools:**
- ✅ `ytmusic_get_playlists` - List user playlists
- ✅ `ytmusic_get_playlist_items` - Get songs in a playlist
- ✅ `ytmusic_get_liked` - Get liked songs
- ✅ `ytmusic_search` - Search for music
- ✅ `ytmusic_create_playlist` - Create new playlist
- ✅ `ytmusic_add_to_playlist` - Add song to playlist
- ✅ `ytmusic_remove_from_playlist` - Remove song from playlist
- ✅ `ytmusic_delete_playlist` - Delete playlist

**Implementation notes:**
- Uses YouTube Data API v3 (`youtube.v3`) via `google-api-python-client`
- Does NOT use `ytmusicapi` library (not installed in venv)
- Authentication via OAuth2 credentials

### 4. Comparison with Spotify

**Location:** `/home/debian/second_brain/.99_Archive/spotify_integration_archived/spotify_tools.py`

Spotify's Web API provides dedicated playback endpoints:

```python
def get_now_playing() -> Optional[Dict[str, Any]]:
    """Get currently playing track."""
    data = spotify_request("GET", "/me/player/currently-playing")
    # Returns: is_playing, track details, progress, device info
```

**Spotify API capabilities:**
- ✅ `GET /me/player/currently-playing` - Real-time current track
- ✅ `GET /me/player` - Full player state
- ✅ `PUT /me/player/play` - Control playback (requires Premium)
- ✅ `POST /me/player/next` - Skip tracks
- ✅ Device targeting for multi-device control

**Required scopes:**
- `user-read-currently-playing`
- `user-read-playback-state`
- `user-modify-playback-state`

---

## Why the Difference?

### Spotify's Approach
Spotify's API is designed for **real-time integration** with active playback sessions. Their business model encourages third-party app integration and cross-platform experiences.

### YouTube Music's Approach
YouTube Music API is designed for **content management** (playlists, library, discovery) rather than player control. Possible reasons:

1. **Architecture:** YouTube Music playback happens client-side (web, mobile apps) without exposing server-side state
2. **Privacy/Security:** Google may intentionally not expose real-time listening data via API
3. **Business Model:** Less emphasis on third-party app ecosystem
4. **Complexity:** Multi-device playback state synchronization may not be server-tracked

---

## Potential Workarounds (Limited)

### Option 1: History-Based Approximation
```python
# Get most recent track from history
history = ytmusic.get_history()
most_recent = history[0] if history else None
```

**Limitations:**
- Only shows last *completed* track, not currently playing
- No real-time progress or device info
- Delay between actual playback and history update
- Won't show anything if user is mid-track

### Option 2: YouTube Data API Watch History
```python
# Our current implementation has get_watch_history()
# Location: interface/server/youtube_tools.py:169
```

**Limitations:**
- Often restricted/disabled by YouTube (`playlistNotFound` error)
- Same issues as Option 1 (completed tracks only)
- Requires special API access

### Option 3: Browser Extension / Desktop App
Create a local client that monitors the YouTube Music web player or desktop app.

**Requirements:**
- Browser extension with page access
- OR desktop app that reads window titles / media keys
- Would need to expose data via local API

**Complexity:** High, and outside scope of our MCP server tools.

---

## Recommendation

**Do not implement "now playing" for YouTube Music.**

### Reasons:
1. **No official API support** - Not a limitation of ytmusicapi, but of YouTube's entire API ecosystem
2. **Workarounds are inadequate** - History-based approximations provide stale data
3. **User expectations** - Users familiar with Spotify's "now playing" would be confused by YouTube Music's delayed/inaccurate version
4. **Maintenance burden** - Fragile workarounds likely to break

### Alternative:
- Document this limitation clearly in user-facing docs
- Suggest users use Spotify integration if real-time playback tracking is important
- If YouTube/Google adds playback state endpoints in the future, we can revisit

---

## References

- **ytmusicapi GitHub:** https://github.com/sigma67/ytmusicapi
- **YouTube Data API v3:** https://developers.google.com/youtube/v3/docs
- **Spotify Web API - Currently Playing:** https://developer.spotify.com/documentation/web-api/reference/get-the-users-currently-playing-track
- **Our Spotify Implementation:** `.99_Archive/spotify_integration_archived/spotify_tools.py:478`
- **Our YouTube Music Implementation:** `interface/server/mcp_tools/youtube/music.py`
