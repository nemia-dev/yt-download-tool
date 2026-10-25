"""Offline tests: no YouTube network access required."""

# Standard Library
import json
import sys
import threading
import types
from pathlib import Path

# 3rd-Party
import pytest

# Project
from yt_downloader.enums import DownloadMode
from yt_downloader.playlist import ResolvedSource
from yt_downloader.playlist import VideoEntry
from yt_downloader.playlist import classify_url
from yt_downloader.playlist import resolve_url
from yt_downloader.queue_manager import QueueManager
from yt_downloader.queue_manager import TaskStatus
from yt_downloader.queue_manager import create_tasks
from yt_downloader.queue_manager import safe_name
from yt_downloader.queue_manager import safe_subfolder
from yt_downloader.transcripts import TranscriptUnavailable
from yt_downloader.transcripts import fetch_text
from yt_downloader.transcripts import select_transcript


def source(playlist_id=None):
    return ResolvedSource(
        playlist_id,
        'Rust: Course/Intro',
        (VideoEntry('abcdefghijk', 'Part 1'), VideoEntry('zyxwvutsrqp', 'Part 2', 2)),
    )


def test_url_classification():
    assert classify_url('https://youtube.com/watch?v=abcdefghijk&list=PLtest123')[0] == 'video'
    assert classify_url('https://youtube.com/playlist?list=PLtest123') == (
        'playlist',
        'https://www.youtube.com/playlist?list=PLtest123',
    )
    assert classify_url('https://youtu.be/abcdefghijk?t=12')[0] == 'video'
    assert classify_url('https://youtube.com/shorts/abcdefghijk')[0] == 'video'
    for url in [
        'https://youtube.com.evil.org/watch?v=abcdefghijk',
        'file:///tmp/foo',
        'https://youtube.com/watch?list=PL123',
        'https://youtube.com/playlist',
    ]:
        with pytest.raises(ValueError):
            classify_url(url)


def test_resolve_playlist_and_video(monkeypatch):
    options_seen = []

    class FakeYdl:
        def __init__(self, options):
            options_seen.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=False):
            if 'playlist?' in url:
                return {
                    'id': 'PLtest123',
                    'title': 'Intro',
                    'entries': [
                        {'id': 'abcdefghijk', 'title': 'Hello'},
                        None,
                        {'id': 'zyxwvutsrqp', 'title': 'World'},
                        {'title': 'No ID'},
                    ],
                }
            return {'id': 'abcdefghijk', 'title': 'Hello'}

    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=FakeYdl))
    logs = []
    result = resolve_url('https://www.youtube.com/playlist?list=PLtest123', log=logs.append)
    assert [v.index for v in result.videos] == [1, 3]
    assert result.playlist_id == 'PLtest123'
    assert len(logs) >= 2
    assert options_seen[0]['noplaylist'] is False
    one = resolve_url('https://www.youtube.com/watch?v=abcdefghijk&list=PLtest123')
    assert len(one.videos) == 1 and one.playlist_id is None
    assert options_seen[1]['noplaylist'] is True


def test_names_and_safe_folder():
    assert safe_name('CON') == '_CON'
    assert '/' not in safe_name('Weird/Name:*')
    assert safe_subfolder('My Project/Week 1') == Path('My Project/Week 1')
    for unsafe in ('../secret', '/etc', 'C:\\Windows', 'foo//bar', '.\\foo'):
        with pytest.raises(ValueError):
            safe_subfolder(unsafe)


def test_destinations_persistence_and_restart(tmp_path):
    manager = QueueManager(tmp_path / 'state.json')
    individual = create_tasks(source(), DownloadMode.TRANSCRIPT, None, tmp_path / 'Transcription')
    playlist = create_tasks(
        source('PL123'), DownloadMode.TRANSCRIPT, None, tmp_path / 'Transcription'
    )
    assert 'Individual' in individual[0].output_path
    assert '002 - ' in playlist[1].output_path
    assert len(manager.snapshot()) == 0
    assert manager.add(individual) == 2
    assert manager.add(individual) == 0
    assert manager.add(playlist) == 2
    manager.update(playlist[0].task_id, TaskStatus.PROCESSING)
    manager.update(playlist[1].task_id, TaskStatus.FAILED, error='timeout')
    manager.set_root(DownloadMode.TRANSCRIPT, tmp_path / 'custom')
    reloaded = QueueManager(tmp_path / 'state.json')
    assert len(reloaded.snapshot()) == 4
    assert (
        next(x for x in reloaded.snapshot() if x.task_id == playlist[0].task_id).status
        == TaskStatus.PENDING
    )
    assert reloaded.root_override(DownloadMode.TRANSCRIPT) == (tmp_path / 'custom').resolve()
    assert reloaded.reset_failed() == 1
    assert reloaded.remove(set(), {individual[0].group_id}) == 2
    assert len(reloaded.snapshot()) == 2
    assert json.loads((tmp_path / 'state.json').read_text())['version'] == 1


def test_language_priority_and_manual_first(monkeypatch):
    class Transcript:
        def __init__(self, code, generated):
            self.language_code, self.is_generated = code, generated

        def fetch(self):
            return [
                types.SimpleNamespace(text='first line'),
                types.SimpleNamespace(text='second line'),
            ]

    tracks = [
        Transcript('fr', False),
        Transcript('en', True),
        Transcript('pl', False),
        Transcript('en', False),
    ]

    class API:
        def list(self, video_id):
            return tracks

    monkeypatch.setitem(
        sys.modules, 'youtube_transcript_api', types.SimpleNamespace(YouTubeTranscriptApi=API)
    )
    assert select_transcript('abcdefghijk', log=lambda msg: None) is tracks[3]
    assert fetch_text('abcdefghijk', log=lambda msg: None) == 'first line\nsecond line\n'
    tracks.clear()
    with pytest.raises(TranscriptUnavailable):
        fetch_text('abcdefghijk', log=lambda msg: None)


def test_caption_not_available_exception(monkeypatch):
    NoTranscriptFound = type('NoTranscriptFound', (Exception,), {})

    class API:
        def list(self, video_id):
            raise NoTranscriptFound('disabled')

    monkeypatch.setitem(
        sys.modules, 'youtube_transcript_api', types.SimpleNamespace(YouTubeTranscriptApi=API)
    )
    with pytest.raises(TranscriptUnavailable):
        select_transcript('abcdefghijk', log=lambda msg: None)


def test_sequential_worker_and_report(tmp_path, monkeypatch):
    # Import queue_worker without installing yt-dlp in the offline test environment.
    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=object))
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    tasks = create_tasks(
        source('PL111'), DownloadMode.TRANSCRIPT, None, tmp_path / 'Transcription'
    )
    manager.add(tasks)
    processed = []

    def mock_process(task, log):
        processed.append(task.video_id)
        if task.task_id == tasks[0].task_id:
            raise TranscriptUnavailable('No subtitles')
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', mock_process)
    logs = []
    counts = queue_worker.run_queue(
        manager, threading.Event(), logs.append, lambda: None, max_concurrent=1
    )
    assert processed == [x.video_id for x in tasks]
    assert counts[TaskStatus.UNAVAILABLE] == 1
    assert counts[TaskStatus.COMPLETED] == 1
    report = (tmp_path / 'Transcription' / 'transcription-report.txt').read_text()
    assert 'No subtitles' in report
    assert 'https://www.youtube.com/watch?v=abcdefghijk' in report


def test_stop_after_current_task(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    tasks = create_tasks(source(), DownloadMode.TRANSCRIPT, None, tmp_path / 'Transcription')
    manager.add(tasks)
    stop = threading.Event()
    count = []

    def process(task, log):
        count.append(task)
        stop.set()
        return TaskStatus.SKIPPED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', process)
    queue_worker.run_queue(manager, stop, lambda *_: None, lambda: None, max_concurrent=1)
    assert len(count) == 1
    assert manager.snapshot()[1].status == TaskStatus.PENDING


def test_restore_missing_completed_output(tmp_path):
    manager = QueueManager(tmp_path / 'state.json')
    task = create_tasks(source(), DownloadMode.TRANSCRIPT, None, tmp_path / 'Transcript')[0]
    manager.add([task])
    Path(task.output_path).parent.mkdir(parents=True)
    Path(task.output_path).write_text('Saved transcript')
    manager.update(task.task_id, TaskStatus.COMPLETED)
    assert QueueManager(manager.path).snapshot()[0].status == TaskStatus.COMPLETED
    Path(task.output_path).unlink()
    assert QueueManager(manager.path).snapshot()[0].status == TaskStatus.PENDING


def test_bad_json_preserves_original_backup(tmp_path):
    state = tmp_path / 'state.json'
    state.write_text('{broken JSON', encoding='utf-8')
    manager = QueueManager(state)
    assert manager.load_warning
    manager.add(create_tasks(source(), DownloadMode.TRANSCRIPT, None, tmp_path / 'T'))
    backups = list(tmp_path.glob('state.json.corrupt-*'))
    assert len(backups) == 1
    assert backups[0].read_text() == '{broken JSON'
    assert len(QueueManager(state).snapshot()) == 2


def test_existing_media_detected(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    task = create_tasks(source(), DownloadMode.AUDIO, '192', tmp_path / 'Audio')[0]
    folder = Path(task.output_dir)
    folder.mkdir(parents=True)
    existing = folder / f'Example [{task.video_id}].mp3'
    existing.write_bytes(b'mp3 bytes')
    monkeypatch.setattr(
        queue_worker, 'download_single_task', lambda *args: pytest.fail('unexpected download')
    )
    status, output = queue_worker.process_task(task, lambda *_: None)
    assert status == TaskStatus.SKIPPED and output == str(existing)


def many_tasks(tmp_path, count=8):
    videos = tuple(VideoEntry(f'{i:011d}', f'Video {i}', i + 1) for i in range(count))
    return create_tasks(
        ResolvedSource('PLmany', 'Batch', videos),
        DownloadMode.TRANSCRIPT,
        None,
        tmp_path / 'Transcription',
    )


def test_concurrent_workers_are_bounded_and_refill(tmp_path, monkeypatch):
    # Standard Library
    import time

    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    tasks = many_tasks(tmp_path, count=9)
    for task in tasks:
        task.mode = DownloadMode.AUDIO
    manager.add(tasks)
    lock = threading.Lock()
    active = maximum = 0

    def mock_process(task, log):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.025)
        with lock:
            active -= 1
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', mock_process)
    counts = queue_worker.run_queue(
        manager, threading.Event(), lambda *_: None, lambda: None, max_concurrent=3
    )
    assert maximum == 3
    assert active == 0
    assert counts[TaskStatus.COMPLETED] == 9
    assert all(t.status == TaskStatus.COMPLETED for t in manager.snapshot())


def test_concurrent_stop_does_not_schedule_more_tasks(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    tasks = many_tasks(tmp_path, count=7)
    for task in tasks:
        task.mode = DownloadMode.AUDIO
    manager.add(tasks)
    started_three = threading.Event()
    release = threading.Event()
    stop = threading.Event()
    lock = threading.Lock()
    started = []
    result = []

    def mock_process(task, log):
        with lock:
            started.append(task.task_id)
            if len(started) == 3:
                started_three.set()
        assert release.wait(4), 'Workers never released'
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', mock_process)
    runner = threading.Thread(
        target=lambda: result.append(
            queue_worker.run_queue(manager, stop, lambda *_: None, lambda: None, max_concurrent=3)
        )
    )
    runner.start()
    try:
        assert started_three.wait(4), 'Three workers failed to start'
        assert len(started) == 3
        assert sum(t.status == TaskStatus.PROCESSING for t in manager.snapshot()) == 3
        stop.set()
    finally:
        release.set()
        runner.join(5)
    assert not runner.is_alive()
    assert len(started) == 3
    assert result[0][TaskStatus.COMPLETED] == 3
    assert result[0][TaskStatus.PENDING] == 4


def test_concurrent_unavailable_and_failures_independent(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    tasks = many_tasks(tmp_path, count=4)
    manager.add(tasks)

    def mock_process(task, log):
        if task.video_id == tasks[0].video_id:
            raise TranscriptUnavailable('Captions disabled')
        if task.video_id == tasks[1].video_id:
            raise TimeoutError('Temporary outage')
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', mock_process)
    counts = queue_worker.run_queue(
        manager,
        threading.Event(),
        lambda *_: None,
        lambda: None,
        max_concurrent=4,
        max_transcripts=2,
        transcript_delay=0,
    )
    assert counts[TaskStatus.UNAVAILABLE] == 1
    assert counts[TaskStatus.FAILED] == 1
    assert counts[TaskStatus.COMPLETED] == 2
    report = (tmp_path / 'Transcription' / 'transcription-report.txt').read_text()
    assert 'Captions disabled' in report
    assert 'Temporary outage' in report


def test_concurrency_setting_is_saved_and_bounded(tmp_path):
    manager = QueueManager(tmp_path / 'queue.json')
    assert manager.get_concurrency(default=7) == 5
    manager.set_concurrency(4)
    assert QueueManager(manager.path).get_concurrency() == 4
    for value in (0, 6):
        with pytest.raises(ValueError):
            manager.set_concurrency(value)
    assert manager.get_concurrency() == 4
