# YT Download Tool v0.5

Tkinter desktop queue manager for YouTube **videos, audio, and caption transcripts**. Supports pasting multiple links, expanding playlists, and restoring an interrupted queue. No speech-to-text model is used: videos without available captions are reported and skipped.

## Install / run

Python 3.12 or newer is required. Install [uv](https://docs.astral.sh/uv/) and run from the project directory:

```bash
uv sync
uv run python src/main.py --gui
```

Alternative GUI entry point after installation: `uv run yt-download-gui`.

Video/audio conversion may require **FFmpeg** installed and available on PATH (in particular MP3 and separate video/audio streams).

## Queue GUI

1. Paste video and/or playlist URLs, **one link per line**.
2. Choose **Video**, **Audio**, or **Transcript** and optionally a quality and a project subfolder. Mode, quality and destination are recorded **when items are added**.
3. Select **Add to queue**. Playlists are discovered in the background. Duplicate videos are ignored only when they have the same mode and output directory.
4. Select **Start / Resume**. **Total parallel (1–5)** controls total simultaneous downloads (default: 3). **Parallel transcripts (1–2)** has its own stricter limit (default: **1**), independently of video/audio jobs. Transcript jobs start at least 2 seconds apart. **Stop** lets current downloads finish, but starts no new tasks and leaves the rest pending.
5. Use **Retry failed**, **Remove selected**, **Clear completed**, **Browse...**, or **Open folder** as needed. Right-click an item to open its containing folder.

A `watch?v=...&list=...` URL adds **one video**. A `playlist?list=...` URL expands the **whole playlist**. Inaccessible entries without a usable ID are reported in the log during expansion.

### Transcript output

```text
Transcription/
├── Individual/
│   └── Tutorial [videoID].txt
├── Rust Basics [playlistID]/
│   ├── 001 - Introduction [videoID].txt
│   └── 002 - Ownership [videoID].txt
└── transcription-report.txt
```

A chosen project subfolder goes between `Transcription/` and `Individual/` or playlist directories. Playlist numbers follow YouTube's original positions.

Transcript selection prefers **English**, then **Polish**, then other available languages. Within each language, manual captions take priority over auto-generated captions. The application first tries `youtube-transcript-api`; if it fails, it uses **yt-dlp's existing subtitle tracks** as a fallback. The fallback fetches only subtitle data; it does not download video or audio or generate speech recognition. It supports VTT, JSON3, SRT, SRV3, and TTML tracks.

If neither method finds captions, the task is **Unavailable**. Ordinary download/network errors become **Failed**. A confirmed **IpBlocked / HTTP 429** is different: when both methods cannot recover, the queue **pauses automatically** rather than attempting the remaining videos. Already-running tasks finish, while unstarted items stay **Pending**. The GUI displays a warning. Do not immediately retry an entire playlist from the same IP; wait and try one transcript later, or reduce Parallel transcripts to 1.

Files are UTF-8 text written atomically. Existing nonempty output files are skipped by default.

The root's `transcription-report.txt` summarizes statuses and lists missing-caption videos and failures, including their URLs. It is updated after a queue run. Reports are created per transcript root when queued tasks target different destinations.

### Persistence

The queue is stored automatically in an atomic JSON state file:

- Windows: `%LOCALAPPDATA%\YTDownloadTool\queue.json`
- Linux: `${XDG_STATE_HOME:-~/.local/state}/YTDownloadTool/queue.json`

An interrupted task becomes pending on restart. Completed entries with missing/empty files become pending again; existing valid outputs are not downloaded again. Browse-folder overrides are remembered separately for each mode. Queue removal **does not delete downloaded files**.

### Task statuses

- **Pending**: waiting to be processed
- **Processing**: task currently downloading (up to configured concurrency)
- **Completed**: file saved
- **Skipped**: existing nonempty output
- **Unavailable**: missing/inaccessible captions (not automatically retried)
- **Failed**: download/API error (can be retried); an IP block automatically pauses the queue

## CLI (preserved)

```bash
uv run python src/main.py "https://youtu.be/VIDEO_ID" -t
uv run python src/main.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" -t
uv run python src/main.py "YOUTUBE_URL" -v -q 720p
uv run python src/main.py "YOUTUBE_URL" -a -q 192
uv run python src/main.py "YOUTUBE_URL" -t -f "Research/2026"
```

The CLI continues to support the existing flags. Transcript mode also handles explicit playlist URLs, uses the same fallback and playlist/individual output folders, and stops the batch on an IP block. The GUI queue state is separate from CLI executions.

## Configuration

Copy `.env.example` to `.env` and configure folders as needed:

```env
DEFAULT_ROOT_FOLDER=""
DEFAULT_VIDEO_FOLDER="Video"
DEFAULT_AUDIO_FOLDER="Audio"
DEFAULT_TRANS_FOLDER="Transcription"
MAX_CONCURRENT_DOWNLOADS=3
```

`MAX_CONCURRENT_DOWNLOADS` sets the initial GUI worker count (clamped to 1–5) and controls the CLI video/audio playlist downloader. The GUI saves the total worker count and the separate transcript concurrency in its queue state. Each worker uses blocking download APIs in a separate thread; no Tkinter widgets are touched by worker threads. YouTube may rate-limit repeated requests, and a fallback method cannot guarantee access when the public IP is blocked.

## Tests

```bash
uv run --with pytest pytest -q tests
```

Tests mock YouTube/API calls and do not require live YouTube access. Live functionality, including fallback subtitle retrieval, depends on YouTube response behavior, captions availability, `yt-dlp` compatibility, and network access.
