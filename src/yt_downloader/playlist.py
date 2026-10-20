"""Normalize YouTube URLs and discover videos without downloading media."""

# Standard Library
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs
from urllib.parse import urlparse


class ResolutionError(ValueError):
    """An input URL or its YouTube metadata cannot be resolved."""


@dataclass(frozen=True)
class VideoEntry:
    video_id: str
    title: str
    index: int | None = None


@dataclass(frozen=True)
class ResolvedSource:
    playlist_id: str | None
    title: str
    videos: tuple[VideoEntry, ...]


_VIDEO_ID = re.compile(r'^[a-zA-Z0-9_-]{6,64}$')
_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}


def classify_url(raw_url: str) -> tuple[str, str]:
    """Return (kind, normalized URL). Watch links with a list still mean ONE video."""
    url = raw_url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in {'https', 'http'} or host not in _HOSTS:
        raise ResolutionError('Use a YouTube video or playlist URL (http/https).')

    query = parse_qs(parsed.query)
    path = parsed.path.rstrip('/')
    if host == 'youtu.be':
        video_id = path.removeprefix('/')
    elif path == '/playlist':
        playlist_id = (query.get('list') or [''])[0]
        if not playlist_id or not re.fullmatch(r'[a-zA-Z0-9_-]+', playlist_id):
            raise ResolutionError('Missing or invalid playlist ID.')
        return 'playlist', f'https://www.youtube.com/playlist?list={playlist_id}'
    elif path == '/watch':
        video_id = (query.get('v') or [''])[0]
    elif path.startswith(('/shorts/', '/live/', '/embed/')):
        video_id = path.split('/')[2] if len(path.split('/')) > 2 else ''
    else:
        raise ResolutionError('Unsupported YouTube URL type.')

    if not _VIDEO_ID.fullmatch(video_id):
        raise ResolutionError('Missing or invalid YouTube video ID.')
    return 'video', f'https://www.youtube.com/watch?v={video_id}'


def _entry_id(entry: dict) -> str | None:
    identifier = entry.get('id')
    if isinstance(identifier, str) and _VIDEO_ID.fullmatch(identifier):
        return identifier
    url = entry.get('url') or entry.get('webpage_url')
    if isinstance(url, str):
        if _VIDEO_ID.fullmatch(url):
            return url
        try:
            kind, normalized = classify_url(url)
            if kind == 'video':
                return parse_qs(urlparse(normalized).query)['v'][0]
        except ResolutionError:
            pass
    return None


def resolve_url(raw_url: str, log: Callable[[str], None] = print) -> ResolvedSource:
    kind, url = classify_url(raw_url)
    # 3rd-Party
    from yt_dlp import YoutubeDL

    options = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'noplaylist': kind == 'video',
        'ignoreerrors': True,
    }
    try:
        with YoutubeDL(options) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise ResolutionError(f'Cannot retrieve YouTube metadata: {exc}') from exc
    if not data:
        raise ResolutionError('YouTube returned no information for this URL.')

    if kind == 'video':
        video_id = _entry_id(data) or parse_qs(urlparse(url).query)['v'][0]
        return ResolvedSource(
            None,
            data.get('title') or video_id,
            (VideoEntry(video_id, data.get('title') or video_id),),
        )

    playlist_id = data.get('id') or parse_qs(urlparse(url).query)['list'][0]
    title = data.get('title') or f'Playlist {playlist_id}'
    entries = list(data.get('entries') or [])
    videos = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            log(f'Skipping inaccessible playlist entry #{index}.')
            continue
        video_id = _entry_id(entry)
        if not video_id:
            log(f'Skipping playlist entry #{index}: no video ID.')
            continue
        videos.append(VideoEntry(video_id, entry.get('title') or video_id, index))
    if not videos:
        raise ResolutionError('Playlist contains no accessible video entries.')
    log(f'Found {len(videos)} videos in playlist: {title}.')
    return ResolvedSource(str(playlist_id), title, tuple(videos))
