# Standard Library
import re
from collections.abc import Callable
from pathlib import Path

# 3rd-Party
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# Project
from yt_downloader.d_types import VideoData

LogFn = Callable[[str], None]


def sanitize_path(path_str: str) -> Path:
    parts = Path(path_str).parts
    cleaned = [re.sub(r'[<>:"\\|?*]', '-', part).strip().strip('.') for part in parts]
    return Path(*cleaned)


def get_video_data(yt_link: str, log: LogFn = print) -> VideoData | None:
    try:
        with YoutubeDL({'download': False, 'extract_flat': True, 'quiet': True}) as ydl:
            info_dict = ydl.extract_info(yt_link, download=False)
    except DownloadError as exc:
        log(f'yt-dlp error: {exc}')
        return None
    except Exception as exc:
        log(f'Unexpected error: {exc}')
        return None

    if not info_dict:
        return None

    if info_dict.get('_type') == 'playlist':
        playlist_title = info_dict.get('title', 'Unknown Playlist')
        videos = list(info_dict.get('entries') or [])

        log(f'Playlist: {playlist_title}')
        log(f'Videos: {len(videos)}')

        return VideoData(
            id=info_dict.get('id'),
            title=playlist_title,
            is_playlist=True,
            entries=videos,
        )

    video_id = info_dict.get('id')
    video_title = info_dict.get('title', 'Unknown Title')
    log(f'Video: {video_title}')

    return VideoData(
        id=video_id,
        title=video_title,
        is_playlist=False,
        entries=[],
    )
