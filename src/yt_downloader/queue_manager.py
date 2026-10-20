"""Thread-safe queue, atomic JSON persistence, and deterministic destinations."""

# Standard Library
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

# Project
from yt_downloader.enums import DownloadMode
from yt_downloader.playlist import ResolvedSource


class TaskStatus(StrEnum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    UNAVAILABLE = 'unavailable'
    FAILED = 'failed'
    SKIPPED = 'skipped'


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.UNAVAILABLE, TaskStatus.FAILED, TaskStatus.SKIPPED}
_WINDOWS_RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)


def safe_name(name: str, max_length: int = 110) -> str:
    """Sanitize one cross-platform filename component, never a path."""
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', name).strip(' .')
    if not result or result in {'.', '..'}:
        result = 'Untitled'
    if _WINDOWS_RESERVED.match(result):
        result = f'_{result}'
    return result[:max_length].rstrip(' .') or 'Untitled'


def safe_subfolder(folder: str | None) -> Path:
    """Allow relative nested subfolders, rejecting traversal and absolute paths."""
    if not folder or not folder.strip():
        return Path()
    normalized = folder.strip().replace('\\', '/')
    parts = normalized.split('/')
    if normalized.startswith('/') or ':' in normalized or any(x in {'', '.', '..'} for x in parts):
        raise ValueError('Folder must be a relative name without .. or drive letters.')
    return Path(*(safe_name(part) for part in parts))


def state_file() -> Path:
    if os.name == 'nt':
        directory = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        directory = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    return directory / 'YTDownloadTool' / 'queue.json'


@dataclass
class QueueTask:
    task_id: str
    video_id: str
    title: str
    url: str
    mode: str
    quality: str | None
    playlist_id: str | None
    playlist_title: str | None
    playlist_index: int | None
    group_id: str
    group_label: str
    root_dir: str
    output_dir: str
    output_path: str = ''
    status: str = TaskStatus.PENDING
    error: str = ''

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.video_id, self.mode, str(Path(self.output_dir).resolve()).casefold())


def create_tasks(
    source: ResolvedSource, mode: DownloadMode, quality: str | None, root: Path
) -> list[QueueTask]:
    root = root.expanduser().resolve()
    if source.playlist_id:
        group_label = f'{safe_name(source.title)} [{safe_name(source.playlist_id, 60)}]'
    else:
        group_label = 'Individual'
    directory = root / group_label
    group_key = f'{mode.value}|{directory}'.casefold()
    group_id = hashlib.sha256(group_key.encode('utf-8')).hexdigest()[:20]
    tasks = []
    for video in source.videos:
        prefix = f'{video.index:03d} - ' if video.index is not None else ''
        output_path = ''
        if mode == DownloadMode.TRANSCRIPT:
            output_path = str(
                directory / f'{prefix}{safe_name(video.title)} [{video.video_id}].txt'
            )
        tasks.append(
            QueueTask(
                task_id=uuid.uuid4().hex,
                video_id=video.video_id,
                title=video.title,
                url=f'https://www.youtube.com/watch?v={video.video_id}',
                mode=mode.value,
                quality=quality,
                playlist_id=source.playlist_id,
                playlist_title=source.title if source.playlist_id else None,
                playlist_index=video.index,
                group_id=group_id,
                group_label=group_label,
                root_dir=str(root),
                output_dir=str(directory),
                output_path=output_path,
            )
        )
    return tasks


class QueueManager:
    def __init__(self, path: Path | None = None):
        self.path = path or state_file()
        self.lock = threading.RLock()
        self.tasks: list[QueueTask] = []
        self.folder_overrides: dict[str, str] = {}
        self.concurrency: int | None = None
        self.load_warning: str = ''
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (
                not isinstance(data, dict)
                or data.get('version') != 1
                or not isinstance(data.get('tasks'), list)
            ):
                raise ValueError('Unsupported queue file format')
            tasks = [QueueTask(**item) for item in data['tasks']]
            for task in tasks:
                if task.status == TaskStatus.PROCESSING:
                    task.status = TaskStatus.PENDING
                    task.error = ''
                elif task.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED):
                    output = Path(task.output_path) if task.output_path else None
                    if output is None or not output.is_file() or output.stat().st_size == 0:
                        task.status = TaskStatus.PENDING
                        task.error = ''
            self.tasks = tasks
            self.folder_overrides = dict(data.get('folder_overrides') or {})
            saved_concurrency = data.get('concurrency')
            if isinstance(saved_concurrency, int) and 1 <= saved_concurrency <= 5:
                self.concurrency = saved_concurrency
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.load_warning = (
                f'Could not load queue state ({exc}); a backup will be kept before any new save.'
            )

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.load_warning and self.path.exists():
            # Standard Library
            import shutil

            backup = self.path.with_name(self.path.name + '.corrupt-' + uuid.uuid4().hex[:8])
            shutil.copy2(self.path, backup)
            self.load_warning = ''
        content = json.dumps(
            {
                'version': 1,
                'tasks': [asdict(t) for t in self.tasks],
                'folder_overrides': self.folder_overrides,
                'concurrency': self.concurrency,
            },
            ensure_ascii=False,
            indent=2,
        )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.path.parent, delete=False, suffix='.tmp'
            ) as f:
                temp_path = Path(f.name)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def snapshot(self) -> list[QueueTask]:
        with self.lock:
            return [replace(task) for task in self.tasks]

    def add(self, tasks: list[QueueTask]) -> int:
        with self.lock:
            existing = {t.identity for t in self.tasks}
            new = []
            for task in tasks:
                if task.identity not in existing:
                    existing.add(task.identity)
                    new.append(task)
            if new:
                self.tasks.extend(new)
                self._save()
            return len(new)

    def update(
        self, task_id: str, status: TaskStatus, *, error: str = '', output_path: str | None = None
    ):
        with self.lock:
            for task in self.tasks:
                if task.task_id == task_id:
                    task.status = status
                    task.error = error
                    if output_path is not None:
                        task.output_path = output_path
                    self._save()
                    return
            raise KeyError(task_id)

    def reset_failed(self) -> int:
        with self.lock:
            count = 0
            for task in self.tasks:
                if task.status == TaskStatus.FAILED:
                    task.status = TaskStatus.PENDING
                    task.error = ''
                    count += 1
            if count:
                self._save()
            return count

    def remove(self, ids: set[str], groups: set[str] | None = None) -> int:
        groups = groups or set()
        with self.lock:
            before = len(self.tasks)
            self.tasks = [
                t for t in self.tasks if t.task_id not in ids and t.group_id not in groups
            ]
            removed = before - len(self.tasks)
            if removed:
                self._save()
            return removed

    def clear_completed(self) -> int:
        with self.lock:
            ids = {
                t.task_id
                for t in self.tasks
                if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
            }
        return self.remove(ids)

    def set_root(self, mode: DownloadMode, path: Path):
        with self.lock:
            self.folder_overrides[mode.value] = str(path.expanduser().resolve())
            self._save()

    def root_override(self, mode: DownloadMode) -> Path | None:
        with self.lock:
            value = self.folder_overrides.get(mode.value)
        return Path(value) if value else None

    def set_concurrency(self, count: int):
        if not 1 <= count <= 5:
            raise ValueError('Concurrent downloads must be between 1 and 5.')
        with self.lock:
            self.concurrency = count
            self._save()

    def get_concurrency(self, default: int = 3) -> int:
        with self.lock:
            return self.concurrency if self.concurrency is not None else max(1, min(5, default))

    def counts(self) -> Counter:
        return Counter(t.status for t in self.snapshot())
