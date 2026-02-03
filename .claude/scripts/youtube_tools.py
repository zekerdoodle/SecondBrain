#!/usr/bin/env python3
"""
YouTube Music integration tools.
Uses YouTube Data API v3 for playlist and music management.
"""

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def get_youtube_service(creds):
    """Build YouTube service from credentials."""
    return build('youtube', 'v3', credentials=creds)


def get_liked_music(service, max_results=50):
    """Get liked music videos (YouTube Music likes sync here)."""
    try:
        # 'LL' is the Liked Videos playlist
        response = service.playlistItems().list(
            part='snippet,contentDetails',
            playlistId='LL',
            maxResults=min(max_results, 50)
        ).execute()

        items = []
        for item in response.get('items', []):
            snippet = item.get('snippet', {})
            items.append({
                'title': snippet.get('title'),
                'channel': snippet.get('videoOwnerChannelTitle'),
                'video_id': snippet.get('resourceId', {}).get('videoId'),
                'added_at': snippet.get('publishedAt')
            })
        return items
    except HttpError as e:
        raise Exception(f"Failed to get liked music: {e}")


def get_playlists(service, max_results=25):
    """Get user's playlists."""
    try:
        response = service.playlists().list(
            part='snippet,contentDetails,status',
            mine=True,
            maxResults=min(max_results, 50)
        ).execute()

        playlists = []
        for item in response.get('items', []):
            snippet = item.get('snippet', {})
            playlists.append({
                'id': item.get('id'),
                'title': snippet.get('title'),
                'description': snippet.get('description', '')[:100],
                'item_count': item.get('contentDetails', {}).get('itemCount', 0),
                'privacy': item.get('status', {}).get('privacyStatus')
            })
        return playlists
    except HttpError as e:
        raise Exception(f"Failed to get playlists: {e}")


def get_playlist_items(service, playlist_id, max_results=50):
    """Get items in a playlist."""
    try:
        response = service.playlistItems().list(
            part='snippet,contentDetails',
            playlistId=playlist_id,
            maxResults=min(max_results, 50)
        ).execute()

        items = []
        for item in response.get('items', []):
            snippet = item.get('snippet', {})
            items.append({
                'title': snippet.get('title'),
                'channel': snippet.get('videoOwnerChannelTitle'),
                'video_id': snippet.get('resourceId', {}).get('videoId'),
                'position': snippet.get('position')
            })
        return items
    except HttpError as e:
        raise Exception(f"Failed to get playlist items: {e}")


def create_playlist(service, title, description='', privacy='private'):
    """Create a new playlist."""
    try:
        response = service.playlists().insert(
            part='snippet,status',
            body={
                'snippet': {
                    'title': title,
                    'description': description
                },
                'status': {
                    'privacyStatus': privacy  # 'private', 'public', or 'unlisted'
                }
            }
        ).execute()
        return {
            'id': response.get('id'),
            'title': response.get('snippet', {}).get('title'),
            'url': f"https://music.youtube.com/playlist?list={response.get('id')}"
        }
    except HttpError as e:
        raise Exception(f"Failed to create playlist: {e}")


def add_to_playlist(service, playlist_id, video_id):
    """Add a video/song to a playlist."""
    try:
        response = service.playlistItems().insert(
            part='snippet',
            body={
                'snippet': {
                    'playlistId': playlist_id,
                    'resourceId': {
                        'kind': 'youtube#video',
                        'videoId': video_id
                    }
                }
            }
        ).execute()
        return {
            'success': True,
            'item_id': response.get('id')
        }
    except HttpError as e:
        raise Exception(f"Failed to add to playlist: {e}")


def remove_from_playlist(service, playlist_item_id):
    """Remove an item from a playlist (needs the playlist item ID, not video ID)."""
    try:
        service.playlistItems().delete(id=playlist_item_id).execute()
        return {'success': True}
    except HttpError as e:
        raise Exception(f"Failed to remove from playlist: {e}")


def search_music(service, query, max_results=10):
    """Search for music videos."""
    try:
        response = service.search().list(
            part='snippet',
            q=query,
            type='video',
            videoCategoryId='10',  # Music category
            maxResults=min(max_results, 25)
        ).execute()

        results = []
        for item in response.get('items', []):
            snippet = item.get('snippet', {})
            results.append({
                'title': snippet.get('title'),
                'channel': snippet.get('channelTitle'),
                'video_id': item.get('id', {}).get('videoId'),
                'description': snippet.get('description', '')[:100],
                'url': f"https://music.youtube.com/watch?v={item.get('id', {}).get('videoId')}"
            })
        return results
    except HttpError as e:
        raise Exception(f"Search failed: {e}")


def get_watch_history(service, max_results=25):
    """
    Get watch history - NOTE: This requires special access and may not work
    for all accounts. YouTube's history playlist 'HL' is often restricted.
    """
    try:
        response = service.playlistItems().list(
            part='snippet',
            playlistId='HL',  # History playlist
            maxResults=min(max_results, 50)
        ).execute()

        items = []
        for item in response.get('items', []):
            snippet = item.get('snippet', {})
            items.append({
                'title': snippet.get('title'),
                'channel': snippet.get('videoOwnerChannelTitle'),
                'video_id': snippet.get('resourceId', {}).get('videoId')
            })
        return items
    except HttpError as e:
        # History access is often restricted
        if 'playlistNotFound' in str(e) or '404' in str(e):
            return {'error': 'Watch history access restricted by YouTube'}
        raise Exception(f"Failed to get watch history: {e}")


def delete_playlist(service, playlist_id):
    """Delete a playlist."""
    try:
        service.playlists().delete(id=playlist_id).execute()
        return {'success': True}
    except HttpError as e:
        raise Exception(f"Failed to delete playlist: {e}")


# Format helpers for nice output
def format_playlist_list(playlists):
    """Format playlists for display."""
    lines = [f"📋 Found {len(playlists)} playlist(s):\n"]
    for p in playlists:
        lines.append(f"  • **{p['title']}** ({p['item_count']} items) [{p['privacy']}]")
        lines.append(f"    ID: `{p['id']}`")
        if p['description']:
            lines.append(f"    {p['description'][:60]}...")
        lines.append("")
    return '\n'.join(lines)


def format_track_list(tracks, header="Tracks"):
    """Format track list for display."""
    lines = [f"🎵 {header} ({len(tracks)} items):\n"]
    for i, t in enumerate(tracks[:25], 1):  # Limit display to 25
        lines.append(f"  {i}. **{t['title']}**")
        lines.append(f"     {t.get('channel', 'Unknown artist')}")
        lines.append("")
    if len(tracks) > 25:
        lines.append(f"  ... and {len(tracks) - 25} more")
    return '\n'.join(lines)


def format_search_results(results):
    """Format search results for display."""
    lines = [f"🔍 Found {len(results)} result(s):\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"  {i}. **{r['title']}**")
        lines.append(f"     {r['channel']}")
        lines.append(f"     `{r['video_id']}` | {r['url']}")
        lines.append("")
    return '\n'.join(lines)
