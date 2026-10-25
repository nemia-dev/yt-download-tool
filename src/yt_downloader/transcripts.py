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


class TranscriptIPBlocked(RuntimeError):
    """YouTube is refusing caption requests from this connection.

    The queue must not automatically retry or start additional tasks.
    """


class TranscriptFallbackError(RuntimeError):
    """yt-dlp could not obtain usable subtitles; this is not 'no captions'."""


def is_ip_block(exc: Exception) -> bool:
    """Recognize explicit IP/rate-limit failures without treating all 403s as blocks."""
    for source in (exc, getattr(exc, '__cause__', None)):
        if source is None:
            continue
        name = type(source).__name__.lower()
        message = str(source).lower()
        if name in {'ipblocked', 'requestblocked', 'toomanyrequests', 'ratelimited'}:
            return True
        if getattr(source, 'status', None) == 429 or getattr(source, 'code', None) == 429:
            return True
        if any(
            s in message
            for s in (
                'ipblocked',
                'ip blocked',
                'ip address has been blocked',
                'too many requests',
                'http error 429',
                'rate limit exceeded',
                "sign in to confirm you're not a bot",
                'sign in to confirm you are not a bot',
            )
        ):
            return True
    return False


def _select_ytdlp_track(info: dict, languages: tuple[str, ...]):
    """Choose one manual/automatic subtitle track matching the API's priority."""
    options = []
    for generated, key in ((False, 'subtitles'), (True, 'automatic_captions')):
        for code, formats in (info.get(key) or {}).items():
            if not isinstance(formats, list):
                continue
            language = str(code).lower().replace('_', '-')
            priority = next(
                (
                    i
                    for i, wanted in enumerate(languages)
                    if language == wanted or language.startswith(wanted + '-')
                ),
                len(languages),
            )
            for item in formats:
                if not isinstance(item, dict) or not item.get('url'):
                    continue
                ext = str(item.get('ext', '')).lower()
                format_rank = {'vtt': 0, 'json3': 1, 'srt': 2, 'srv3': 3, 'ttml': 4}.get(ext)
                if format_rank is not None:
                    options.append(
                        (
                            (priority, generated, language, format_rank),
                            code,
                            ext,
                            item['url'],
                            generated,
                        )
                    )
    if not options:
        raise TranscriptUnavailable('yt-dlp found no supported caption tracks.')
    _, code, ext, url, generated = min(options, key=lambda item: item[0])
    return code, ext, url, generated


def _parse_captions(raw: bytes, ext: str) -> str:
    """Convert a YouTube subtitle track to plain UTF-8 text (no timestamps)."""
    # Standard Library
    import html
    import json
    import re
    import xml.etree.ElementTree as ET

    decoded = raw.decode('utf-8-sig', errors='replace')
    lines = []
    if ext == 'json3':
        data = json.loads(decoded)
        for event in data.get('events', []):
            phrase = ''.join(seg.get('utf8', '') for seg in event.get('segs', []))
            if phrase.strip():
                lines.extend(phrase.splitlines())
    elif ext in {'srv3', 'ttml'}:
        root = ET.fromstring(decoded)
        for element in root.iter():
            if element.tag.split('}')[-1] in {'p', 'text'}:
                phrase = ''.join(element.itertext()).strip()
                if phrase:
                    lines.append(phrase)
    elif ext in {'vtt', 'srt'}:
        for line in decoded.splitlines():
            s = line.strip()
            if not s or s.startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
                continue
            if '-->' in s or s.isdecimal():
                continue
            # WebVTT may carry voice/style tags and inline timing tags.
            s = re.sub(r'<[^>]*>', '', s)
            if s:
                lines.append(s)
    else:
        raise TranscriptFallbackError(f'Unsupported yt-dlp caption format: {ext}')

    cleaned = []
    for line in lines:
        s = html.unescape(str(line)).strip()
        if s and (not cleaned or cleaned[-1] != s):
            cleaned.append(s)
    if not cleaned:
        raise TranscriptFallbackError(f'yt-dlp returned empty or unreadable {ext} captions.')
    return '\n'.join(cleaned) + '\n'


def fetch_text_ytdlp(
    video_id: str, *, languages: tuple[str, ...] = ('en', 'pl'), log: Callable[[str], None] = print
) -> str:
    """Fallback: get caption metadata and subtitle bytes only; never fetch video/audio."""
    # 3rd-Party
    from yt_dlp import YoutubeDL

    url = f'https://www.youtube.com/watch?v={video_id}'
    try:
        with YoutubeDL(
            {
                'skip_download': True,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 20,
                'retries': 1,
                'extractor_retries': 1,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise TranscriptFallbackError('yt-dlp returned no video metadata.')
            code, ext, subtitle_url, generated = _select_ytdlp_track(info, languages)
            log(f'yt-dlp captions: {code} ({"auto-generated" if generated else "manual"}, {ext})')
            with ydl.urlopen(subtitle_url) as response:
                # Caption tracks are small; reject unusually large responses.
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise TranscriptFallbackError('Subtitle response exceeds 8 MiB.')
            return _parse_captions(raw, ext)
    except (TranscriptUnavailable, TranscriptFallbackError):
        raise
    except Exception as exc:
        if is_ip_block(exc):
            raise TranscriptIPBlocked(
                f'yt-dlp requests were rate-limited or IP-blocked: {exc}'
            ) from exc
        raise TranscriptFallbackError(f'yt-dlp could not retrieve captions: {exc}') from exc


def fetch_text_with_fallback(video_id: str, *, log: Callable[[str], None] = print) -> str:
    """Try the transcript API, then use yt-dlp captions on any retrieval failure.

    An explicit IP block is never silently converted into 'no captions' or
    an ordinary failure. The caller can pause the queue after fallback fails.
    """
    try:
        return fetch_text(video_id, log=log)
    except Exception as primary:
        blocked = is_ip_block(primary)
        log(f'Transcript API failed ({type(primary).__name__}); trying yt-dlp subtitles.')
        try:
            return fetch_text_ytdlp(video_id, log=log)
        except Exception as secondary:
            if blocked or isinstance(secondary, TranscriptIPBlocked) or is_ip_block(secondary):
                raise TranscriptIPBlocked(
                    f'YouTube blocked transcript requests. API: {primary}; yt-dlp: {secondary}'
                ) from secondary
            if isinstance(primary, TranscriptUnavailable) and isinstance(
                secondary, TranscriptUnavailable
            ):
                raise TranscriptUnavailable(
                    'No accessible captions using either method.'
                ) from secondary
            raise TranscriptFallbackError(
                f'Both subtitle methods failed. API: {primary}; yt-dlp: {secondary}'
            ) from secondary
