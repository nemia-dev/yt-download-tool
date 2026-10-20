# Standard Library
import asyncio
from collections.abc import Callable
from pathlib import Path

# 3rd-Party
from yt_dlp import YoutubeDL

# Project
from yt_downloader.enums import DownloadMode

LogFn = Callable[[str], None]


def get_download_options(output_template: str, choice_type: DownloadMode, quality: str) -> dict:
    """Generate yt-dlp options for video or audio downloads."""
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
        if quality == '1080p':
            ydl_opts['format'] = (
                'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
            )
        elif quality == '720p':
            ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'
        else:
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'

    return ydl_opts


def download_single_task(url: str, ydl_opts: dict):
    """Synchronous worker function, suitable for execution in a thread."""
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
    log: LogFn = print,
):
    tasks = []
    semaphore = asyncio.Semaphore(int(max_concurrent))

    total_videos = len(entries)
    padding = len(str(total_videos))

    log(f'Scheduling {total_videos} downloads, max {max_concurrent} at once...')

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
        tasks.append(download_with_semaphore(semaphore, video_url, opts))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    failed = 0
    for result in results:
        if isinstance(result, Exception):
            failed += 1
            log(f'Download failed: {result}')

    if failed:
        log(f'Playlist finished with {failed} failed download(s).')
    else:
        log('All playlist downloads completed.')
