# Standard Library
import asyncio
from pathlib import Path

# 3rd-Party
from environs import Env
from youtube_transcript_api import YouTubeTranscriptApi

# Project
from yt_downloader.d_types import VideoData
from yt_downloader.downloaders import download_playlist_async
from yt_downloader.downloaders import download_single_task
from yt_downloader.downloaders import get_download_options
from yt_downloader.enums import DownloadMode
from yt_downloader.helpers import get_video_data
from yt_downloader.helpers import sanitize_path

env = Env()
env.read_env()

MAX_CONCURRENT_DOWNLOADS = env.int('MAX_CONCURRENT_DOWNLOADS', 3)
DEFAULT_ROOT_FOLDER = env.str('DEFAULT_ROOT_FOLDER')
DEFAULT_VIDEO_FOLDER = env.str('DEFAULT_VIDEO_FOLDER')
DEFAULT_AUDIO_FOLDER = env.str('DEFAULT_AUDIO_FOLDER')
DEFAULT_TRANS_FOLDER = env.str('DEFAULT_TRANS_FOLDER')

ROOT_FOLDER = Path(DEFAULT_ROOT_FOLDER)
VIDEO_FOLDER = ROOT_FOLDER / DEFAULT_VIDEO_FOLDER
AUDIO_FOLDER = ROOT_FOLDER / DEFAULT_AUDIO_FOLDER
TRANS_FOLDER = ROOT_FOLDER / DEFAULT_TRANS_FOLDER


def main():
    print('Starting the YT Downloader')

    # === Input
    yt_link = input('YouTube link: ')

    # === Getting the data
    video_data: VideoData = get_video_data(yt_link)
    if not video_data:
        print('Could not fetch video information.')
        return

    title = video_data['title']
    is_playlist = video_data['is_playlist']
    entries = video_data['entries']

    # === Confirm
    con = input('Is this the video?[y/n]: ')
    if con and con.lower() != 'y':
        print('Sorry for the mistake.')
        return

    # === Download choices
    # Folder to where to download them
    folder_name = input(f'Folder name (Default: {title}): ')
    if not folder_name:
        folder_name = title

    folder_name = sanitize_path(folder_name)

    if is_playlist:
        print("It's a playlist = the downloaded videos will be ordered")

    format_choice = None
    user_format_choice = input('Which format would you like to download it? [v/a/t]: ')
    if user_format_choice:
        match user_format_choice.lower():
            case 'v':
                format_choice = DownloadMode.VIDEO
            case 'a':
                format_choice = DownloadMode.AUDIO
            case 't':
                format_choice = DownloadMode.TRANSCRIPT
            case _:
                print('No format specified.')
                return

    match format_choice:
        case DownloadMode.VIDEO:
            quality_choice = '1080p'
            quality_list = ['1080p', '720p', 'best']

            user_quality_choice = input(f'Resolution {quality_list}: ')
            if user_quality_choice in quality_list:
                quality_choice = user_quality_choice

            download_dir = VIDEO_FOLDER / folder_name

        case DownloadMode.AUDIO:
            quality_choice = '192'
            quality_list = ['128', '192', '320']

            user_quality_choice = input(f'Audio bitrate {quality_list} kbps: ')
            if user_quality_choice in quality_list:
                quality_choice = user_quality_choice

            download_dir = AUDIO_FOLDER / folder_name

        case DownloadMode.TRANSCRIPT:
            download_dir = TRANS_FOLDER

    # Checking if it exists
    if not download_dir.exists():
        # create
        download_dir.mkdir(parents=True)

    # === Downloading

    if format_choice != DownloadMode.TRANSCRIPT:
        if not is_playlist:
            output_template = str(download_dir / '%(title)s.%(ext)s')
            opts = get_download_options(
                output_template,
                format_choice,
                quality_choice,
            )
            download_single_task(yt_link, opts)

        else:
            if not entries:
                print('No entries to download.')
                return

            asyncio.run(
                download_playlist_async(
                    entries,
                    download_dir,
                    format_choice,
                    quality_choice,
                    max_concurrent=MAX_CONCURRENT_DOWNLOADS,
                )
            )

    else:
        video_id = video_data['id']

        if not video_id:
            print('Could not determine the YouTube video ID.')
            return

        try:
            transcript = YouTubeTranscriptApi().fetch(video_id)

            transcript_text = '\n'.join(snippet.text for snippet in transcript)

            transcript_file = download_dir / f'{sanitize_path(title)}.txt'
            transcript_file.write_text(
                transcript_text,
                encoding='utf-8',
            )

            print(f'Transcript saved to: {transcript_file}')

        except Exception as e:
            print(f'Could not fetch transcript: {e}')


if __name__ == '__main__':
    main()
