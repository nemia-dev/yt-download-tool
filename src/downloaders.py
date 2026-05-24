# Standard Library
import asyncio
from pathlib import Path

# 3rd-Party
from enums import DownloadMode
from yt_dlp import YoutubeDL


def get_download_options(output_template: str, choice_type: DownloadMode, quality: str) -> dict:
    """Generates yt-dlp options based on user preferences.

    choice_type: 'mp4' (video) or 'mp3' (audio)
    quality: 'best', '1080p', '720p', etc.
    """

    # Base configuration options
    ydl_opts = {
        'outtmpl': str(output_template),
    }

    if choice_type == DownloadMode.AUDIO:
        ydl_opts.update(
            {
                'format': 'bestaudio/best',
                'keepvideo': False,
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': quality,
                    }
                ],
            }
        )

    elif choice_type == DownloadMode.VIDEO:
        # Configure for Video MP4 extraction
        if quality == '1080p':
            ydl_opts['format'] = (
                'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
            )
        elif quality == '720p':
            ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
        else:
            # General best quality MP4 fallback
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'

    return ydl_opts


def download_single_task(url: str, ydl_opts: dict):
    """Synchronous worker function executed inside a thread."""
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


async def download_with_semaphore(semaphore, video_url, opts):
    async with semaphore:
        await asyncio.to_thread(download_single_task, video_url, opts)


async def download_playlist_async(
    entries: list,
    download_dir: Path,
    format_choice: DownloadMode,
    quality_choice: str,
    max_concurrent: int = 3,
):
    tasks = []
    semaphore = asyncio.Semaphore(int(max_concurrent))

    total_videos = len(entries)
    padding = len(str(total_videos))

    print(f'Scheduling {total_videos} downloads, max {max_concurrent} at once...')

    for index, entry in enumerate(entries, start=1):
        video_url = entry.get('webpage_url')

        if not video_url:
            video_id = entry.get('id') or entry.get('url')
            if not video_id:
                continue

            video_url = f'https://www.youtube.com/watch?v={video_id}'

        prefix = str(index).zfill(padding)
        output_template = str(download_dir / f'{prefix} - %(title)s.%(ext)s')

        opts = get_download_options(output_template, format_choice, quality_choice)

        task = download_with_semaphore(semaphore, video_url, opts)
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            print(f'Download failed: {result}')

    print('\nAll playlist downloads completed!')
