"""Caption-only transcript retrieval; this module never downloads audio."""

# Standard Library
import os
import tempfile
from collections.abc import Callable
from pathlib import Path


class TranscriptUnavailable(RuntimeError):
    """No accessible captions for the requested video."""


_UNAVAILABLE_ERRORS = {'NoTranscriptFound', 'TranscriptsDisabled', 'NotTranslatable'}


def select_transcript(
    video_id: str, *, languages: tuple[str, ...] = ('en', 'pl'), log: Callable[[str], None] = print
):
    # 3rd-Party
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        transcripts = list(YouTubeTranscriptApi().list(video_id))
    except Exception as exc:
        if type(exc).__name__ in _UNAVAILABLE_ERRORS:
            raise TranscriptUnavailable(f'No accessible captions: {exc}') from exc
        raise
    if not transcripts:
        raise TranscriptUnavailable('No accessible captions for this video.')

    def rank(item):
        code = str(item.language_code).lower().replace('_', '-')
        language_rank = next(
            (
                n
                for n, wanted in enumerate(languages)
                if code == wanted or code.startswith(wanted + '-')
            ),
            len(languages),
        )
        return (language_rank, bool(item.is_generated))

    transcript = min(transcripts, key=rank)
    source = 'auto-generated' if transcript.is_generated else 'manual'
    log(f'Captions: {transcript.language_code} ({source})')
    return transcript


def fetch_text(video_id: str, *, log: Callable[[str], None] = print) -> str:
    selected = select_transcript(video_id, log=log)
    try:
        result = selected.fetch()
    except Exception as exc:
        if type(exc).__name__ in _UNAVAILABLE_ERRORS:
            raise TranscriptUnavailable(f'Captions unavailable: {exc}') from exc
        raise
    text = '\n'.join(str(item.text) for item in result).strip()
    if not text:
        raise TranscriptUnavailable('Caption track contains no text.')
    return text + '\n'


def save_transcript(path: Path, text: str) -> Path:
    """Store a complete UTF-8 transcript atomically, never a partially saved file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return path
