# Standard Library
import argparse

# Project
from yt_downloader.constants import AUDIO_QUALITIES
from yt_downloader.constants import DEFAULT_AUDIO_QUALITY
from yt_downloader.constants import DEFAULT_VIDEO_QUALITY
from yt_downloader.constants import VIDEO_QUALITIES
from yt_downloader.enums import DownloadMode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='yt-download',
        description='Download YouTube video, audio, or a transcript.',
    )
    parser.add_argument('url', nargs='?', help='YouTube video or playlist URL')

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '-v',
        '--video',
        dest='mode',
        action='store_const',
        const=DownloadMode.VIDEO,
        help='download video and skip the format prompt',
    )
    mode_group.add_argument(
        '-a',
        '--audio',
        dest='mode',
        action='store_const',
        const=DownloadMode.AUDIO,
        help='download MP3 audio and skip the format prompt',
    )
    mode_group.add_argument(
        '-t',
        '--transcript',
        dest='mode',
        action='store_const',
        const=DownloadMode.TRANSCRIPT,
        help='download transcript and skip the format prompt',
    )

    parser.add_argument(
        '-q',
        '--quality',
        help='video: 1080p/720p/best; audio: 128/192/320',
    )
    parser.add_argument(
        '-f',
        '--folder',
        help='output subfolder name (also supported for transcripts)',
    )
    parser.add_argument(
        '--gui',
        action='store_true',
        help='open the graphical interface',
    )
    return parser


def prompt_mode() -> DownloadMode:
    while True:
        choice = input('Format [v/a/t]: ').strip().lower()
        match choice:
            case 'v':
                return DownloadMode.VIDEO
            case 'a':
                return DownloadMode.AUDIO
            case 't':
                return DownloadMode.TRANSCRIPT
            case _:
                print('Choose v, a, or t.')


def default_quality(mode: DownloadMode) -> str | None:
    if mode == DownloadMode.VIDEO:
        return DEFAULT_VIDEO_QUALITY
    if mode == DownloadMode.AUDIO:
        return DEFAULT_AUDIO_QUALITY
    return None


def prompt_quality(mode: DownloadMode) -> str | None:
    if mode == DownloadMode.TRANSCRIPT:
        return None

    allowed = VIDEO_QUALITIES if mode == DownloadMode.VIDEO else AUDIO_QUALITIES
    fallback = default_quality(mode)
    value = input(f'Quality {list(allowed)} (default: {fallback}): ').strip()
    return value or fallback


def run_cli(args: argparse.Namespace) -> int:
    url = args.url or input('YouTube link: ').strip()
    if not url:
        print('A YouTube URL is required.')
        return 2

    mode = args.mode or prompt_mode()

    quality = args.quality
    if quality is None and args.mode is None:
        quality = prompt_quality(mode)
    elif quality is None:
        quality = default_quality(mode)

    if mode == DownloadMode.VIDEO and quality not in VIDEO_QUALITIES:
        print(f'Error: video quality must be one of: {", ".join(VIDEO_QUALITIES)}')
        return 2
    if mode == DownloadMode.AUDIO and quality not in AUDIO_QUALITIES:
        print(f'Error: audio quality must be one of: {", ".join(AUDIO_QUALITIES)}')
        return 2
    if mode == DownloadMode.TRANSCRIPT:
        quality = None

    folder = args.folder
    if folder is None and args.mode is None:
        if mode == DownloadMode.TRANSCRIPT:
            folder = (
                input('Transcript folder (optional, leave empty for Transcription/): ').strip()
                or None
            )
        else:
            folder = (
                input('Folder name (leave empty to use the video/playlist title): ').strip()
                or None
            )

    try:
        # Project
        from yt_downloader.app import download

        download(
            url,
            mode,
            quality=quality,
            folder_name=folder,
        )
    except (RuntimeError, ValueError) as exc:
        print(f'Error: {exc}')
        return 1
    except Exception as exc:
        print(f'Download failed: {exc}')
        return 1

    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.gui:
        # Project
        from yt_downloader.gui import main as gui_main

        gui_main()
        return 0

    return run_cli(args)
