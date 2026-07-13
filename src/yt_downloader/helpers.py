# Standard Library
import re
from pathlib import Path

# 3rd-Party
from d_types import VideoData
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


def sanitize_path(path_str: str) -> Path:
    parts = Path(path_str).parts

    cleaned = [re.sub(r'[<>:"\\|?*]', '-', part).strip().strip('.') for part in parts]

    return Path(*cleaned)


def get_video_data(yt_link: str) -> VideoData | None:
    info_dict = {}
    try:
        with YoutubeDL({'download': False, 'extract_flat': True}) as ydl:
            info_dict = ydl.extract_info(yt_link, download=False)

    except DownloadError as e:
        print(f'yt-dlp error: {e}')
        return None

    except Exception as e:
        print(f'Unexpected error: {e}')
        return None

    if not info_dict:
        return None

    if info_dict.get('_type') == 'playlist':
        playlist_title = info_dict.get('title', 'Unknown Playlist')
        videos = info_dict.get('entries', [])  # The list of videos in the playlist

        print('--- Playlist Info ---')
        print(f'Playlist Title: {playlist_title}')
        print(f'Total Videos: {len(videos)}\n')

        # Loop through individual videos inside the playlist
        print('Videos in this playlist:')
        for index, video in enumerate(videos, start=1):
            # Note: In 'extract_flat' mode, duration is available but views/formats are skipped for speed
            v_title = video.get('title', 'Unknown Title')
            v_duration = video.get('duration', 0)
            v_url = video.get('url', '')

            print(f'[{index}] {v_title} ({v_duration} seconds) - URL: {v_url}')

        return VideoData(title=playlist_title, is_playlist=True, entries=videos)

    else:
        video_id = info_dict.get('id')
        video_title = info_dict.get('title', 'Unknown Title')
        video_duration = info_dict.get('duration', 0)  # In seconds
        view_count = info_dict.get('view_count', 0)

        print(f'ID: {video_id}')
        print(f'Title: {video_title}')
        print(f'Duration: {video_duration} seconds')
        print(f'Views: {view_count}')

        return VideoData(id=video_id, title=video_title, is_playlist=False, entries=[])
