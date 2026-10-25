"""Isolated regression tests for yt-dlp subtitle fallback and IP-block protection."""

# Standard Library
import io
import json
import sys
import threading
import time
import types

# 3rd-Party
import pytest

# Project
from yt_downloader import transcripts
from yt_downloader.enums import DownloadMode
from yt_downloader.playlist import ResolvedSource
from yt_downloader.playlist import VideoEntry
from yt_downloader.queue_manager import QueueManager
from yt_downloader.queue_manager import TaskStatus
from yt_downloader.queue_manager import create_tasks

try:
    # 3rd-Party
    import yt_dlp  # noqa: F401
except ImportError:
    sys.modules.setdefault('yt_dlp', types.SimpleNamespace(YoutubeDL=object))

# Project
from yt_downloader.queue_worker import run_queue


def make_tasks(tmp_path, count, mode=DownloadMode.TRANSCRIPT):
    source = ResolvedSource(
        None, 'Sample', tuple(VideoEntry(f'{i:011d}', f'Clip {i}') for i in range(count))
    )
    return create_tasks(source, mode, None, tmp_path / 'Transcription')


def fake_ydl(monkeypatch, info, data, *, expected_ext='vtt'):
    class YoutubeDL:
        def __init__(self, options):
            assert options['skip_download'] is True
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            return info

        def urlopen(self, url):
            assert url == 'https://example.test/subtitles'
            return io.BytesIO(data)

    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=YoutubeDL))


def test_vtt_fallback_succeeds_after_api_ip_block(monkeypatch):
    IpBlocked = type('IpBlocked', (Exception,), {})

    def blocked(*args, **kwargs):
        raise IpBlocked('your IP address has been blocked')

    monkeypatch.setattr(transcripts, 'fetch_text', blocked)
    fake_ydl(
        monkeypatch,
        {'subtitles': {'en': [{'ext': 'vtt', 'url': 'https://example.test/subtitles'}]}},
        b'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello there\n\n00:00:04.000 --> 00:00:06.000\nWorld\n',
    )
    logs = []
    assert (
        transcripts.fetch_text_with_fallback('abcdefghijk', log=logs.append)
        == 'Hello there\nWorld\n'
    )
    assert any('yt-dlp captions:' in line for line in logs)


def test_fallback_preserves_language_and_manual_priority(monkeypatch):
    info = {
        'subtitles': {
            'pl': [{'ext': 'vtt', 'url': 'https://example.test/subtitles'}],
            'en': [{'ext': 'json3', 'url': 'https://example.test/subtitles'}],
        },
        'automatic_captions': {'en': [{'ext': 'vtt', 'url': 'https://example.test/subtitles'}]},
    }
    fake_ydl(
        monkeypatch,
        info,
        json.dumps(
            {'events': [{'segs': [{'utf8': 'Line one'}]}, {'segs': [{'utf8': 'Line two'}]}]}
        ).encode(),
    )
    assert (
        transcripts.fetch_text_ytdlp('abcdefghijk', log=lambda *_: None) == 'Line one\nLine two\n'
    )


def test_no_captions_stays_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise transcripts.TranscriptUnavailable('No captions')

    monkeypatch.setattr(transcripts, 'fetch_text', unavailable)
    fake_ydl(monkeypatch, {'subtitles': {}, 'automatic_captions': {}}, b'')
    with pytest.raises(transcripts.TranscriptUnavailable):
        transcripts.fetch_text_with_fallback('abcdefghijk', log=lambda *_: None)


def test_api_ip_block_and_fallback_failure_escalates(monkeypatch):
    class IpBlocked(Exception):
        pass

    def blocked(*args, **kwargs):
        raise IpBlocked('IP blocked')

    monkeypatch.setattr(transcripts, 'fetch_text', blocked)
    fake_ydl(monkeypatch, {'subtitles': {}, 'automatic_captions': {}}, b'')
    with pytest.raises(transcripts.TranscriptIPBlocked):
        transcripts.fetch_text_with_fallback('abcdefghijk', log=lambda *_: None)


def test_ytdlp_http429_is_ip_block(monkeypatch):
    def unavailable(*args, **kwargs):
        raise transcripts.TranscriptUnavailable('No captions')

    monkeypatch.setattr(transcripts, 'fetch_text', unavailable)

    class YoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError('HTTP Error 429: Too Many Requests')

    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=YoutubeDL))
    with pytest.raises(transcripts.TranscriptIPBlocked):
        transcripts.fetch_text_with_fallback('abcdefghijk', log=lambda *_: None)


def test_circuit_breaker_leaves_pending_and_reports_reason(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    manager.add(make_tasks(tmp_path, 5))
    seen = []
    blocked = []
    stop = threading.Event()

    def process(task, log):
        seen.append(task.video_id)
        raise transcripts.TranscriptIPBlocked('API + yt-dlp refused requests')

    monkeypatch.setattr(queue_worker, 'process_task', process)
    status = run_queue(
        manager,
        stop,
        lambda *_: None,
        lambda: None,
        max_concurrent=5,
        max_transcripts=1,
        transcript_delay=0,
        on_ip_block=blocked.append,
    )
    assert len(seen) == 1
    assert len(blocked) == 1
    assert stop.is_set()
    assert status[TaskStatus.FAILED] == 1
    assert status[TaskStatus.PENDING] == 4
    assert 'IpBlocked' in manager.snapshot()[0].error
    report = (tmp_path / 'Transcription' / 'transcription-report.txt').read_text()
    assert 'API + yt-dlp refused requests' in report
    assert QueueManager(manager.path).snapshot()[1].status == TaskStatus.PENDING


def test_inflight_tasks_finish_when_circuit_trips(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    manager.add(make_tasks(tmp_path, 5))
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    running = []
    error = []

    def process(task, log):
        with lock:
            running.append(task.video_id)
            if len(running) == 2:
                started.set()
        assert release.wait(4)
        if task.video_id == '00000000000':
            raise transcripts.TranscriptIPBlocked('IP block')
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', process)
    thread = threading.Thread(
        target=lambda: error.append(
            run_queue(
                manager,
                threading.Event(),
                lambda *_: None,
                lambda: None,
                max_concurrent=5,
                max_transcripts=2,
                transcript_delay=0,
            )
        )
    )
    thread.start()
    try:
        assert started.wait(3)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(running) == 2
    assert error[0][TaskStatus.FAILED] == 1
    assert error[0][TaskStatus.COMPLETED] == 1
    assert error[0][TaskStatus.PENDING] == 3


def test_transcript_and_media_limits_are_separate(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    tasks = make_tasks(tmp_path, 6)
    for i, task in enumerate(tasks):
        if i % 2:
            task.mode = DownloadMode.AUDIO
    manager.add(tasks)
    active = transcript_active = 0
    max_total = max_transcript = 0
    lock = threading.Lock()

    def process(task, log):
        nonlocal active, transcript_active, max_total, max_transcript
        with lock:
            active += 1
            transcript_active += task.mode == DownloadMode.TRANSCRIPT
            max_total = max(max_total, active)
            max_transcript = max(max_transcript, transcript_active)
        time.sleep(0.035)
        with lock:
            active -= 1
            transcript_active -= task.mode == DownloadMode.TRANSCRIPT
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', process)
    counts = run_queue(
        manager,
        threading.Event(),
        lambda *_: None,
        lambda: None,
        max_concurrent=3,
        max_transcripts=1,
        transcript_delay=0,
    )
    assert max_total == 3
    assert max_transcript == 1
    assert counts[TaskStatus.COMPLETED] == 6


def test_transcript_cooldown_and_persisted_limit(tmp_path, monkeypatch):
    # Project
    from yt_downloader import queue_worker

    manager = QueueManager(tmp_path / 'state.json')
    manager.set_transcript_concurrency(2)
    assert QueueManager(manager.path).get_transcript_concurrency() == 2
    for invalid in (0, 3, True):
        with pytest.raises(ValueError):
            manager.set_transcript_concurrency(invalid)
    manager.add(make_tasks(tmp_path, 3))
    times = []

    def process(task, log):
        times.append(time.monotonic())
        return TaskStatus.COMPLETED, task.output_path

    monkeypatch.setattr(queue_worker, 'process_task', process)
    counts = run_queue(
        manager,
        threading.Event(),
        lambda *_: None,
        lambda: None,
        max_concurrent=3,
        max_transcripts=2,
        transcript_delay=0.06,
    )
    assert counts[TaskStatus.COMPLETED] == 3
    assert all((b - a) >= 0.04 for a, b in zip(times, times[1:], strict=False))


def test_rate_limit_recognition_avoids_unrelated_403():
    assert transcripts.is_ip_block(RuntimeError('HTTP Error 429: Too Many Requests'))
    assert transcripts.is_ip_block(type('IpBlocked', (Exception,), {})('quota'))
    assert not transcripts.is_ip_block(RuntimeError('HTTP Error 403: Forbidden'))
    assert not transcripts.is_ip_block(transcripts.TranscriptUnavailable('No subtitles'))
