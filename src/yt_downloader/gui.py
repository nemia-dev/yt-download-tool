"""Tkinter queue manager for mixed video, audio, and transcript batches."""

# Standard Library
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

# Project
from yt_downloader.app import validate_quality
from yt_downloader.config import load_config
from yt_downloader.constants import AUDIO_QUALITIES
from yt_downloader.constants import DEFAULT_AUDIO_QUALITY
from yt_downloader.constants import DEFAULT_VIDEO_QUALITY
from yt_downloader.constants import VIDEO_QUALITIES
from yt_downloader.enums import DownloadMode
from yt_downloader.playlist import resolve_url
from yt_downloader.queue_manager import TERMINAL
from yt_downloader.queue_manager import QueueManager
from yt_downloader.queue_manager import TaskStatus
from yt_downloader.queue_manager import create_tasks
from yt_downloader.queue_manager import safe_subfolder
from yt_downloader.queue_worker import run_queue


def open_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(path))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(path)])
    else:
        subprocess.Popen(['xdg-open', str(path)])


class DownloaderGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('YT Download Tool — Queue Manager')
        self.geometry('940x730')
        self.minsize(700, 560)
        self.config_data = load_config()
        self.manager = QueueManager()
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.job = ''
        self.pause_reason = None
        self._group_expansion: dict[str, bool] = {}
        self.mode_var = tk.StringVar(value=DownloadMode.TRANSCRIPT.value)
        self.quality_var = tk.StringVar()
        self.concurrency_var = tk.StringVar(
            value=str(self.manager.get_concurrency(self.config_data.max_concurrent_downloads))
        )
        self.transcript_concurrency_var = tk.StringVar(
            value=str(self.manager.get_transcript_concurrency())
        )
        self.folder_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.progress_var = tk.StringVar(value='0 / 0 processed')
        self.count_var = tk.StringVar(value='0 videos')
        self.status_var = tk.StringVar(value='Ready')
        self.show_logs = tk.BooleanVar(value=True)

        self._build_ui()
        self.folder_var.trace_add('write', lambda *_: self._update_output_label())
        self._sync_mode_fields()
        self._refresh_tree()
        if self.manager.load_warning:
            self._append_log(self.manager.load_warning)
            messagebox.showwarning('Queue state', self.manager.load_warning)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(100, self._poll_messages)

    def _build_ui(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=3)
        frame.rowconfigure(10, weight=1)

        ttk.Label(frame, text='YouTube URLs — one video or playlist link per line').grid(
            row=0, column=0, sticky='w'
        )
        input_frame = ttk.Frame(frame)
        input_frame.grid(row=1, column=0, sticky='nsew', pady=(4, 5))
        input_frame.columnconfigure(0, weight=1)
        self.urls = tk.Text(input_frame, height=3, wrap='none', undo=True)
        self.urls.grid(row=0, column=0, sticky='nsew')
        url_scroll = ttk.Scrollbar(input_frame, command=self.urls.yview)
        url_scroll.grid(row=0, column=1, sticky='ns')
        self.urls.configure(yscrollcommand=url_scroll.set)
        self.add_button = ttk.Button(frame, text='+ Add to queue', command=self._add_urls)
        self.add_button.grid(row=2, column=0, sticky='e', pady=(0, 8))

        settings = ttk.Frame(frame)
        settings.grid(row=3, column=0, sticky='ew', pady=(0, 7))
        for col in range(5):
            settings.columnconfigure(col, weight=1)
        ttk.Label(settings, text='Download as').grid(row=0, column=0, sticky='w')
        ttk.Label(settings, text='Quality').grid(row=0, column=1, sticky='w')
        ttk.Label(settings, text='Project subfolder (optional)').grid(row=0, column=2, sticky='w')
        ttk.Label(settings, text='Total parallel').grid(row=0, column=3, sticky='w')
        ttk.Label(settings, text='Parallel transcripts').grid(row=0, column=4, sticky='w')
        radio_frame = ttk.Frame(settings)
        radio_frame.grid(row=1, column=0, sticky='w')
        self.mode_buttons = []
        for label, value in (('Video', 'video'), ('Audio', 'audio'), ('Transcript', 'transcript')):
            btn = ttk.Radiobutton(
                radio_frame,
                text=label,
                variable=self.mode_var,
                value=value,
                command=self._sync_mode_fields,
            )
            btn.pack(side='left', padx=(0, 10))
            self.mode_buttons.append(btn)
        self.quality_box = ttk.Combobox(
            settings, textvariable=self.quality_var, state='readonly', width=12
        )
        self.quality_box.grid(row=1, column=1, sticky='w')
        self.folder_entry = ttk.Entry(settings, textvariable=self.folder_var)
        self.folder_entry.grid(row=1, column=2, sticky='ew')
        self.concurrency_box = ttk.Combobox(
            settings,
            textvariable=self.concurrency_var,
            values=('1', '2', '3', '4', '5'),
            state='readonly',
            width=6,
        )
        self.concurrency_box.grid(row=1, column=3, sticky='w', padx=(12, 0))
        self.transcript_concurrency_box = ttk.Combobox(
            settings,
            textvariable=self.transcript_concurrency_var,
            values=('1', '2'),
            state='readonly',
            width=5,
        )
        self.transcript_concurrency_box.grid(row=1, column=4, sticky='w', padx=(12, 0))

        destination = ttk.Frame(frame)
        destination.grid(row=4, column=0, sticky='ew', pady=(0, 12))
        destination.columnconfigure(1, weight=1)
        ttk.Label(destination, text='Output:').grid(row=0, column=0, sticky='w', padx=(0, 8))
        ttk.Label(destination, textvariable=self.output_var, anchor='w').grid(
            row=0, column=1, sticky='ew'
        )
        self.browse_button = ttk.Button(destination, text='Browse...', command=self._browse_folder)
        self.browse_button.grid(row=0, column=2, padx=(5, 5))
        self.open_folder_button = ttk.Button(
            destination, text='Open folder', command=self._open_output
        )
        self.open_folder_button.grid(row=0, column=3)

        queue_header = ttk.Frame(frame)
        queue_header.grid(row=5, column=0, sticky='ew')
        ttk.Label(queue_header, text='Download queue', font=('Segoe UI', 10, 'bold')).pack(
            side='left'
        )
        ttk.Label(queue_header, textvariable=self.count_var).pack(side='right')

        listing = ttk.Frame(frame)
        listing.grid(row=6, column=0, sticky='nsew', pady=(4, 7))
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            listing,
            columns=('mode', 'status', 'details'),
            show='tree headings',
            selectmode='extended',
        )
        self.tree.heading('#0', text='Playlist / Video')
        self.tree.heading('mode', text='Mode')
        self.tree.heading('status', text='Status')
        self.tree.heading('details', text='Reason')
        self.tree.column('#0', width=360, minwidth=200, stretch=True)
        self.tree.column('mode', width=90, minwidth=70, stretch=False)
        self.tree.column('status', width=105, minwidth=85, stretch=False)
        self.tree.column('details', width=250, minwidth=100, stretch=True)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Button-3>', self._show_context_menu)
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(
            label='Open containing folder', command=self._open_selected_folder
        )
        self.context_menu.add_command(label='Remove selection', command=self._remove_selected)

        progress = ttk.Frame(frame)
        progress.grid(row=7, column=0, sticky='ew')
        progress.columnconfigure(0, weight=1)
        ttk.Label(progress, textvariable=self.progress_var).grid(row=0, column=0, sticky='w')
        self.progress_bar = ttk.Progressbar(
            progress, orient='horizontal', mode='determinate', maximum=100
        )
        self.progress_bar.grid(row=1, column=0, sticky='ew', pady=(3, 6))

        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, sticky='ew', pady=(0, 7))
        self.start_button = ttk.Button(actions, text='Start / Resume', command=self._start_queue)
        self.stop_button = ttk.Button(actions, text='Stop', command=self._stop_queue)
        self.retry_button = ttk.Button(actions, text='Retry failed', command=self._retry_failed)
        self.remove_button = ttk.Button(
            actions, text='Remove selected', command=self._remove_selected
        )
        self.clear_button = ttk.Button(
            actions, text='Clear completed', command=self._clear_completed
        )
        for btn in (
            self.start_button,
            self.stop_button,
            self.retry_button,
            self.remove_button,
            self.clear_button,
        ):
            btn.pack(side='left', padx=(0, 6))

        log_header = ttk.Frame(frame)
        log_header.grid(row=9, column=0, sticky='ew')
        ttk.Checkbutton(
            log_header, text='Show logs', variable=self.show_logs, command=self._toggle_logs
        ).pack(side='left')
        ttk.Button(log_header, text='Clear log', command=self._clear_log).pack(side='right')
        self.log = tk.Text(frame, height=6, state='disabled', wrap='word')
        self.log.grid(row=10, column=0, sticky='nsew', pady=(4, 4))
        ttk.Label(frame, textvariable=self.status_var, anchor='e').grid(
            row=11, column=0, sticky='ew'
        )

    def _toggle_logs(self):
        if self.show_logs.get():
            self.log.grid()
        else:
            self.log.grid_remove()

    def _base_root(self, mode: DownloadMode) -> Path:
        override = self.manager.root_override(mode)
        if override is not None:
            return override
        return {
            DownloadMode.VIDEO: self.config_data.video_folder,
            DownloadMode.AUDIO: self.config_data.audio_folder,
            DownloadMode.TRANSCRIPT: self.config_data.transcript_folder,
        }[mode]

    def _effective_root(self) -> Path:
        return (
            (
                self._base_root(DownloadMode(self.mode_var.get()))
                / safe_subfolder(self.folder_var.get())
            )
            .expanduser()
            .resolve()
        )

    def _update_output_label(self):
        try:
            self.output_var.set(str(self._effective_root()))
        except ValueError:
            self.output_var.set('Invalid subfolder name')

    def _sync_mode_fields(self):
        mode = DownloadMode(self.mode_var.get())
        if mode == DownloadMode.VIDEO:
            self.quality_box.configure(values=VIDEO_QUALITIES, state='readonly')
            self.quality_var.set(DEFAULT_VIDEO_QUALITY)
        elif mode == DownloadMode.AUDIO:
            self.quality_box.configure(values=AUDIO_QUALITIES, state='readonly')
            self.quality_var.set(DEFAULT_AUDIO_QUALITY)
        else:
            self.quality_box.configure(values=(), state='disabled')
            self.quality_var.set('')
        self._update_output_label()

    def _browse_folder(self):
        base = self._base_root(DownloadMode(self.mode_var.get())).expanduser().resolve()
        selected = filedialog.askdirectory(
            title='Choose output root folder',
            initialdir=str(base if base.exists() else Path.home()),
        )
        if selected:
            self.manager.set_root(DownloadMode(self.mode_var.get()), Path(selected))
            self._update_output_label()

    def _open_output(self):
        try:
            open_directory(self._effective_root())
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot open folder', str(exc))

    def _selected_ids(self) -> tuple[set[str], set[str]]:
        task_ids, groups = set(), set()
        for row in self.tree.selection():
            if row.startswith('t:'):
                task_ids.add(row[2:])
            elif row.startswith('g:'):
                groups.add(row[2:])
        return task_ids, groups

    def _open_selected_folder(self):
        selected = self.tree.selection()
        if not selected:
            return
        row = selected[0]
        for task in self.manager.snapshot():
            if row == f't:{task.task_id}' or row == f'g:{task.group_id}':
                try:
                    open_directory(Path(task.output_dir))
                except OSError as exc:
                    messagebox.showerror('Cannot open folder', str(exc))
                return

    def _show_context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.context_menu.entryconfigure(
                'Remove selection', state='disabled' if self.busy else 'normal'
            )
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _append_log(self, message: str):
        self.log.configure(state='normal')
        self.log.insert('end', str(message) + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _log_worker(self, message: str):
        self.messages.put(('log', message))

    def _refresh_tree(self):
        selected = set(self.tree.selection())
        for item in self.tree.get_children(''):
            self._group_expansion[item] = bool(self.tree.item(item, 'open'))
        self.tree.delete(*self.tree.get_children(''))
        snapshot = self.manager.snapshot()
        groups = {}
        for task in snapshot:
            groups.setdefault(task.group_id, []).append(task)
        for group_id, tasks in groups.items():
            first = tasks[0]
            complete = sum(t.status in TERMINAL for t in tasks)
            group_row = 'g:' + group_id
            self.tree.insert(
                '',
                'end',
                iid=group_row,
                text=f'{first.group_label}',
                values=(first.mode.title(), f'{complete}/{len(tasks)}', ''),
                open=self._group_expansion.get(group_row, True),
            )
            for task in tasks:
                detail = ' '.join(task.error.split())[:140] if task.error else ''
                row = 't:' + task.task_id
                label = (
                    f'{task.playlist_index:03d} - {task.title}'
                    if task.playlist_index is not None
                    else task.title
                )
                self.tree.insert(
                    group_row,
                    'end',
                    iid=row,
                    text=label,
                    values=(task.mode.title(), task.status.title(), detail),
                )
        for row in selected:
            if self.tree.exists(row):
                self.tree.selection_add(row)
        counts = Counter(t.status for t in snapshot)
        completed = sum(counts[s] for s in TERMINAL)
        count = len(snapshot)
        self.progress_var.set(
            f'{completed} / {count} processed  •  {counts[TaskStatus.COMPLETED]} completed  •  {counts[TaskStatus.UNAVAILABLE]} no captions  •  {counts[TaskStatus.FAILED]} failed'
        )
        self.progress_bar['value'] = (100 * completed / count) if count else 0
        self.count_var.set(f'{count} videos')
        self._sync_buttons(counts)

    def _sync_buttons(self, counts: Counter):
        disabled = 'disabled' if self.busy else 'normal'
        for btn in (
            self.add_button,
            self.browse_button,
            self.remove_button,
            self.clear_button,
            self.retry_button,
        ):
            btn.configure(state=disabled)
        self.start_button.configure(
            state='normal' if not self.busy and counts[TaskStatus.PENDING] else 'disabled'
        )
        self.stop_button.configure(
            state='normal'
            if self.busy and self.job == 'run' and not self.stop_event.is_set()
            else 'disabled'
        )
        if not counts[TaskStatus.FAILED] or self.busy:
            self.retry_button.configure(state='disabled')

    def _set_busy(self, job: str):
        self.busy = True
        self.job = job
        self.urls.configure(state='disabled')
        self.folder_entry.configure(state='disabled')
        for btn in self.mode_buttons:
            btn.configure(state='disabled')
        if self.mode_var.get() != DownloadMode.TRANSCRIPT.value:
            self.quality_box.configure(state='disabled')
        if job == 'run':
            self.concurrency_box.configure(state='disabled')
            self.transcript_concurrency_box.configure(state='disabled')
        self.status_var.set('Resolving URLs...' if job == 'add' else 'Processing queue...')
        self._refresh_tree()

    def _finish_job(self):
        self.busy = False
        self.job = ''
        self.urls.configure(state='normal')
        self.folder_entry.configure(state='normal')
        self.concurrency_box.configure(state='readonly')
        self.transcript_concurrency_box.configure(state='readonly')
        for btn in self.mode_buttons:
            btn.configure(state='normal')
        self._sync_mode_fields()
        self.status_var.set('Ready')
        self._refresh_tree()

    def _add_urls(self):
        urls = [line.strip() for line in self.urls.get('1.0', 'end').splitlines() if line.strip()]
        if not urls:
            messagebox.showerror('Missing URLs', 'Paste one or more YouTube URLs first.')
            return
        try:
            root = self._effective_root()
            mode = DownloadMode(self.mode_var.get())
            quality = validate_quality(mode, self.quality_var.get() or None)
        except ValueError as exc:
            messagebox.showerror('Invalid settings', str(exc))
            return
        self._set_busy('add')
        threading.Thread(
            target=self._resolve_worker, args=(urls, mode, quality, root), daemon=True
        ).start()

    def _resolve_worker(self, urls, mode, quality, root):
        added = 0
        failed = 0
        for url in urls:
            try:
                source = resolve_url(url, log=self._log_worker)
                tasks = create_tasks(source, mode, quality, root)
                count = self.manager.add(tasks)
                added += count
                self._log_worker(f'Added {count}/{len(tasks)} tasks: {source.title}')
                self.messages.put(('refresh', None))
            except Exception as exc:
                failed += 1
                self._log_worker(f'Cannot add {url}: {exc}')
        self.messages.put(('done', f'Added {added} tasks; {failed} URL(s) failed.'))

    def _start_queue(self):
        if not any(t.status == TaskStatus.PENDING for t in self.manager.snapshot()):
            return
        try:
            concurrent = int(self.concurrency_var.get())
            self.manager.set_concurrency(concurrent)
            transcript_concurrent = int(self.transcript_concurrency_var.get())
            self.manager.set_transcript_concurrency(transcript_concurrent)
        except ValueError as exc:
            messagebox.showerror('Invalid concurrency', str(exc))
            return
        self.stop_event.clear()
        self.pause_reason = None
        self._set_busy('run')
        threading.Thread(
            target=self._queue_worker, args=(concurrent, transcript_concurrent), daemon=True
        ).start()

    def _queue_worker(self, concurrent: int, transcript_concurrent: int):
        try:
            run_queue(
                self.manager,
                self.stop_event,
                self._log_worker,
                lambda: self.messages.put(('refresh', None)),
                max_concurrent=concurrent,
                max_transcripts=transcript_concurrent,
                on_ip_block=lambda reason: self.messages.put(('blocked', reason)),
            )
            self.messages.put(('done', 'Queue processing finished.'))
        except Exception as exc:
            self.messages.put(('done', f'Queue worker stopped unexpectedly: {exc}'))

    def _stop_queue(self):
        self.stop_event.set()
        self.status_var.set('Stopping after active downloads...')
        self._append_log(
            'Stop requested; waiting for running downloads to finish. No new tasks will start.'
        )
        self._refresh_tree()

    def _retry_failed(self):
        count = self.manager.reset_failed()
        self._append_log(f'Queued {count} failed task(s) for retry.')
        self._refresh_tree()

    def _remove_selected(self):
        if self.busy:
            return
        tasks, groups = self._selected_ids()
        if not tasks and not groups:
            return
        removed = self.manager.remove(tasks, groups)
        self._append_log(f'Removed {removed} queued task(s); files on disk were not deleted.')
        self._refresh_tree()

    def _clear_completed(self):
        if self.busy:
            return
        number = self.manager.clear_completed()
        self._append_log(f'Cleared {number} completed/existing task(s) from queue.')
        self._refresh_tree()

    def _poll_messages(self):
        try:
            while True:
                kind, message = self.messages.get_nowait()
                if kind == 'log':
                    self._append_log(str(message))
                elif kind == 'blocked':
                    self.pause_reason = str(message)
                elif kind == 'refresh':
                    self._refresh_tree()
                elif kind == 'done':
                    self._append_log(str(message))
                    self._finish_job()
                    if self.pause_reason:
                        self.status_var.set('Paused: YouTube blocked transcript requests')
                        messagebox.showwarning(
                            'Transcript requests blocked',
                            'YouTube blocked transcript requests. The queue was paused and remaining items are still Pending.\n\n'
                            'Try again later, preferably with 1 parallel transcript. Repeated immediate retries may prolong the restriction.',
                        )
                        self.pause_reason = None
        except queue.Empty:
            pass
        self.after(100, self._poll_messages)

    def _close(self):
        if self.busy and not messagebox.askyesno(
            'Close YT Download Tool?', 'A task is active. Closing may interrupt it. Continue?'
        ):
            return
        self.stop_event.set()
        self.destroy()


def main():
    DownloaderGui().mainloop()


if __name__ == '__main__':
    main()
