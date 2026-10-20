# Standard Library
import asyncio
from collections.abc import Callable
from pathlib import Path

# Project
from yt_downloader.config import AppConfig
from yt_downloader.config import load_config
from yt_downloader.constants import AUDIO_QUALITIES
from yt_downloader.constants import DEFAULT_AUDIO_QUALITY
from yt_downloader.constants import DEFAULT_VIDEO_QUALITY
from yt_downloader.constants import VIDEO_QUALITIES
from yt_downloader.downloaders import download_playlist_async
from yt_downloader.downloaders import download_single_task
from yt_downloader.downloaders import get_download_options
from yt_downloader.enums import DownloadMode
from yt_downloader.helpers import get_video_data
from yt_downloader.helpers import sanitize_path

LogFn = Callable[[str], None]


def default_quality(mode: DownloadMode) -> str | None:
    if mode == DownloadMode.VIDEO:
        return DEFAULT_VIDEO_QUALITY
    if mode == DownloadMode.AUDIO:
        return DEFAULT_AUDIO_QUALITY
    return None


def validate_quality(mode: DownloadMode, quality: str | None) -> str | None:
    if mode == DownloadMode.TRANSCRIPT:
        return None

    quality = quality or default_quality(mode)
    allowed = VIDEO_QUALITIES if mode == DownloadMode.VIDEO else AUDIO_QUALITIES

    if quality not in allowed:
        raise ValueError(f'Invalid quality {quality!r}. Allowed values: {", ".join(allowed)}')

    return quality


def _download_directory(
    config: AppConfig,
    mode: DownloadMode,
    folder_name: str | None,
) -> Path:
    if mode == DownloadMode.VIDEO:
        if not folder_name:
            raise ValueError('A video output folder name is required.')
        return config.video_folder / sanitize_path(folder_name)

    if mode == DownloadMode.AUDIO:
        if not folder_name:
            raise ValueError('An audio output folder name is required.')
        return config.audio_folder / sanitize_path(folder_name)

    if folder_name:
        return config.transcript_folder / sanitize_path(folder_name)
    return config.transcript_folder


def download(
    url: str,
    mode: DownloadMode,
    *,
    quality: str | None = None,
    folder_name: str | None = None,
    config: AppConfig | None = None,
    log: LogFn = print,
) -> Path:
    """Download a YouTube URL using one non-interactive operation.

    The CLI and GUI both call this function.
    """
    config = config or load_config()
    quality = validate_quality(mode, quality)

    # Caption-only CLI path also supports explicit YouTube playlists. The GUI
    # operates through the persistent queue, but both reuse the same processor.
    if mode == DownloadMode.TRANSCRIPT:
        # Project
        from yt_downloader.playlist import resolve_url
        from yt_downloader.queue_manager import TaskStatus
        from yt_downloader.queue_manager import create_tasks
        from yt_downloader.queue_manager import safe_subfolder
        from yt_downloader.queue_worker import process_task
        from yt_downloader.transcripts import TranscriptUnavailable

        source = resolve_url(url, log=log)
        root = config.transcript_folder / safe_subfolder(folder_name)
        tasks = create_tasks(source, mode, None, root)
        unavailable = failed = 0
        for task in tasks:
            try:
                status, result = process_task(task, log=log)
                task.status = status
                task.output_path = result
            except TranscriptUnavailable as exc:
                task.status = TaskStatus.UNAVAILABLE
                task.error = str(exc)
                unavailable += 1
                log(f'No captions: {task.title}: {exc}')
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                failed += 1
                log(f'Failed: {task.title}: {exc}')
        if len(tasks) == 1 and (unavailable or failed):
            raise RuntimeError(tasks[0].error)
        # Project
        from yt_downloader.queue_worker import write_transcription_reports

        write_transcription_reports(tasks, log=log)
        log(
            f'Transcript batch finished: {len(tasks) - unavailable - failed} saved/existing, {unavailable} without captions, {failed} failed.'
        )
        return root

    video_data = get_video_data(url, log=log)
    if not video_data:
        raise RuntimeError('Could not fetch video information.')

    title = video_data['title']
    is_playlist = video_data['is_playlist']
    entries = video_data['entries'] or []

    # Video/audio retain the existing behavior of using the video/playlist title
    # as the default subfolder. Transcripts stay directly in Transcription unless
    # the caller explicitly supplies a folder.
    output_folder = folder_name
    if mode != DownloadMode.TRANSCRIPT and not output_folder:
        output_folder = title

    download_dir = _download_directory(config, mode, output_folder)
    download_dir.mkdir(parents=True, exist_ok=True)

    if not is_playlist:
        output_template = str(download_dir / '%(title)s.%(ext)s')
        opts = get_download_options(output_template, mode, quality)
        download_single_task(url, opts)
        log(f'Download completed: {download_dir}')
        return download_dir

    if not entries:
        raise RuntimeError('No entries to download.')

    asyncio.run(
        download_playlist_async(
            entries,
            download_dir,
            mode,
            quality,
            max_concurrent=config.max_concurrent_downloads,
            log=log,
        )
    )
    return download_dir
