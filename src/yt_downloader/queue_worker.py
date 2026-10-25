"""Concurrent queue executor; all Tk interactions happen elsewhere."""

# Standard Library
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
from pathlib import Path

# Project
from yt_downloader.downloaders import download_single_task
from yt_downloader.downloaders import get_download_options
from yt_downloader.enums import DownloadMode
from yt_downloader.queue_manager import QueueManager
from yt_downloader.queue_manager import QueueTask
from yt_downloader.queue_manager import TaskStatus
from yt_downloader.transcripts import TranscriptIPBlocked
from yt_downloader.transcripts import TranscriptUnavailable
from yt_downloader.transcripts import fetch_text_with_fallback
from yt_downloader.transcripts import save_transcript

LogFn = Callable[[str], None]
_REFRESH = Callable[[], None]
_MEDIA_EXTENSIONS = {'.mp3', '.m4a', '.opus', '.mp4', '.mkv', '.webm'}


def find_media(task: QueueTask) -> Path | None:
    output_dir = Path(task.output_dir)
    if not output_dir.exists():
        return None
    suffix = f' [{task.video_id}]'
    for path in output_dir.iterdir():
        if (
            path.is_file()
            and path.suffix.lower() in _MEDIA_EXTENSIONS
            and path.stem.endswith(suffix)
            and path.stat().st_size > 0
        ):
            return path
    return None


def process_task(task: QueueTask, log: LogFn = print) -> tuple[TaskStatus, str]:
    output_dir = Path(task.output_dir)
    mode = DownloadMode(task.mode)
    if mode == DownloadMode.TRANSCRIPT:
        target = Path(task.output_path)
        if target.is_file() and target.stat().st_size > 0:
            log(f'Already exists: {target.name}')
            return TaskStatus.SKIPPED, str(target)
        text = fetch_text_with_fallback(task.video_id, log=log)
        save_transcript(target, text)
        log(f'Saved: {target}')
        return TaskStatus.COMPLETED, str(target)

    existing = find_media(task)
    if existing:
        log(f'Already exists: {existing.name}')
        return TaskStatus.SKIPPED, str(existing)

    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f'{task.playlist_index:03d} - ' if task.playlist_index is not None else ''
    template = str(output_dir / f'{prefix}%(title).120B [%(id)s].%(ext)s')
    options = get_download_options(template, mode, task.quality)
    options.update({'noplaylist': True, 'overwrites': False})
    download_single_task(task.url, options)
    result = find_media(task)
    if result is None:
        raise RuntimeError('yt-dlp completed, but the downloaded output file was not found.')
    log(f'Saved: {result}')
    return TaskStatus.COMPLETED, str(result)


def write_transcription_reports(tasks: list[QueueTask], log: LogFn = print):
    """Create a human-readable report in every affected transcription root."""
    roots = {Path(t.root_dir) for t in tasks if t.mode == DownloadMode.TRANSCRIPT}
    for root in roots:
        group = [
            t for t in tasks if t.mode == DownloadMode.TRANSCRIPT and Path(t.root_dir) == root
        ]
        counts = Counter(t.status for t in group)
        lines = [
            'YT Download Tool - Transcript queue report',
            f'Total: {len(group)}',
            f'Completed: {counts[TaskStatus.COMPLETED]}',
            f'Existing/skipped: {counts[TaskStatus.SKIPPED]}',
            f'Unavailable captions: {counts[TaskStatus.UNAVAILABLE]}',
            f'Failed: {counts[TaskStatus.FAILED]}',
            f'Pending: {counts[TaskStatus.PENDING]}',
            '',
        ]
        for label, status in [
            ('Unavailable captions', TaskStatus.UNAVAILABLE),
            ('Failed', TaskStatus.FAILED),
        ]:
            lines.extend([label, '-' * len(label)])
            for task in group:
                if task.status == status:
                    lines.extend(
                        [
                            f'{task.title} [{task.video_id}]',
                            f'  {task.url}',
                            f'  Playlist: {task.playlist_title or "Individual"}',
                            f'  Reason: {task.error or "No details"}',
                            '',
                        ]
                    )
            if not any(t.status == status for t in group):
                lines.append('None\n')
        try:
            save_transcript(root / 'transcription-report.txt', '\n'.join(lines) + '\n')
            log(f'Report: {root / "transcription-report.txt"}')
        except OSError as exc:
            log(f'Could not save transcript report: {exc}')


def run_queue(
    manager: QueueManager,
    stop: threading.Event,
    log: LogFn,
    refresh: _REFRESH,
    *,
    max_concurrent: int = 3,
    max_transcripts: int = 1,
    transcript_delay: float = 2.0,
    on_ip_block: Callable[[str], None] | None = None,
) -> Counter:
    """Run a bounded queue with an independent transcript limit.

    A recognized IP block trips a circuit breaker: pending tasks remain queued,
    in-flight downloads are allowed to finish, and no new task is submitted.
    Workers never change Tk or QueueManager state directly.
    """
    if not 1 <= max_concurrent <= 5:
        raise ValueError('Concurrent downloads must be between 1 and 5.')
    if not 1 <= max_transcripts <= 2:
        raise ValueError('Parallel transcripts must be between 1 and 2.')
    if transcript_delay < 0:
        raise ValueError('Transcript request delay cannot be negative.')

    pending = [task for task in manager.snapshot() if task.status == TaskStatus.PENDING]
    in_flight = {}
    next_transcript_at = 0.0
    breaker_tripped = False

    def is_transcript(task: QueueTask) -> bool:
        return task.mode == DownloadMode.TRANSCRIPT

    def schedule_next(pool: ThreadPoolExecutor) -> bool:
        nonlocal next_transcript_at
        if stop.is_set() or len(in_flight) >= max_concurrent:
            return False
        transcript_active = sum(is_transcript(task) for task in in_flight.values())
        now = time.monotonic()
        for index, task in enumerate(pending):
            if is_transcript(task) and (
                transcript_active >= max_transcripts or now < next_transcript_at
            ):
                continue
            pending.pop(index)
            manager.update(task.task_id, TaskStatus.PROCESSING)
            refresh()
            log(f'Processing: {task.title} ({task.mode})')
            future = pool.submit(process_task, task, log)
            in_flight[future] = task
            if is_transcript(task):
                next_transcript_at = time.monotonic() + transcript_delay
            return True
        return False

    log(
        f'Starting queue: max {max_concurrent} total, max {max_transcripts} transcript request(s), {transcript_delay:g}s spacing.'
    )
    with ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix='yt-download') as pool:
        while pending or in_flight:
            while schedule_next(pool):
                pass
            if not in_flight:
                if stop.is_set():
                    break
                # Only transcript cooldown can prevent scheduling when nothing is active.
                stop.wait(min(max(0.01, next_transcript_at - time.monotonic()), 0.25))
                continue
            done, _ = wait(in_flight, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                task = in_flight.pop(future)
                try:
                    status, destination = future.result()
                except TranscriptIPBlocked as exc:
                    manager.update(task.task_id, TaskStatus.FAILED, error=f'IpBlocked: {exc}')
                    log(f'IP BLOCKED: {task.title}: {exc}')
                    if not breaker_tripped:
                        breaker_tripped = True
                        stop.set()
                        log(
                            'QUEUE PAUSED: YouTube is blocking transcript requests. Remaining tasks were left pending; do not retry immediately.'
                        )
                        if on_ip_block is not None:
                            on_ip_block(str(exc))
                except TranscriptUnavailable as exc:
                    manager.update(task.task_id, TaskStatus.UNAVAILABLE, error=str(exc))
                    log(f'No captions: {task.title}: {exc}')
                except Exception as exc:
                    manager.update(
                        task.task_id, TaskStatus.FAILED, error=f'{type(exc).__name__}: {exc}'
                    )
                    log(f'Failed: {task.title}: {exc}')
                else:
                    manager.update(task.task_id, status, output_path=destination)
                refresh()

    if stop.is_set():
        log('Queue stopped; remaining tasks stay pending.')

    snapshot = manager.snapshot()
    write_transcription_reports(snapshot, log)
    result = Counter(t.status for t in snapshot)
    log(
        f'Queue summary: completed={result[TaskStatus.COMPLETED]}, existing={result[TaskStatus.SKIPPED]}, no captions={result[TaskStatus.UNAVAILABLE]}, failed={result[TaskStatus.FAILED]}, pending={result[TaskStatus.PENDING]}.'
    )
    return result
