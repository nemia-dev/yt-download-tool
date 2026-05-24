# Standard Library
import asyncio
from pathlib import Path

# 3rd-Party
from d_types import VideoData
from downloaders import download_playlist_async
from downloaders import download_single_task
from downloaders import get_download_options
from enums import DownloadMode
from environs import Env
from helpers import get_video_data
from helpers import sanitize_path

env = Env()
env.read_env()

MAX_CONCURRENT_DOWNLOADS = env.int('MAX_CONCURRENT_DOWNLOADS', 3)
DEFAULT_ROOT_FOLDER = env.str('DEFAULT_ROOT_FOLDER')
DEFAULT_VIDEO_FOLDER = env.str('DEFAULT_VIDEO_FOLDER')
DEFAULT_AUDIO_FOLDER = env.str('DEFAULT_AUDIO_FOLDER')

ROOT_FOLDER = Path(DEFAULT_ROOT_FOLDER)
VIDEO_FOLDER = ROOT_FOLDER / DEFAULT_VIDEO_FOLDER
AUDIO_FOLDER = ROOT_FOLDER / DEFAULT_AUDIO_FOLDER


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

    format_choice = DownloadMode.AUDIO
    user_format_choice = input('Which format would you like to download it? [v/a]: ')
    if user_format_choice and user_format_choice.lower() == 'v':
        format_choice = DownloadMode.VIDEO

    if format_choice == DownloadMode.VIDEO:
        quality_choice = '1080p'
        quality_list = ['1080p', '720p', 'best']

        user_quality_choice = input(f'Resolution {quality_list}: ')
        if user_quality_choice in quality_list:
            quality_choice = user_quality_choice

    else:
        quality_choice = '192'
        quality_list = ['128', '192', '320']

        user_quality_choice = input(f'Audio bitrate {quality_list} kbps: ')
        if user_quality_choice in quality_list:
            quality_choice = user_quality_choice

    # Creating the path
    if format_choice == DownloadMode.AUDIO:
        download_dir = AUDIO_FOLDER / folder_name
    else:
        download_dir = VIDEO_FOLDER / folder_name

    # Checking if it exists
    if not download_dir.exists():
        # create
        download_dir.mkdir(parents=True)

    # === Downloading
    if not is_playlist:
        output_template = str(download_dir / '%(title)s.%(ext)s')
        opts = get_download_options(output_template, format_choice, quality_choice)
        download_single_task(yt_link, opts)
    else:
        if not entries:
            print('No entries to download.')

        asyncio.run(
            download_playlist_async(
                entries,
                download_dir,
                format_choice,
                quality_choice,
                max_concurrent=MAX_CONCURRENT_DOWNLOADS,
            )
        )


if __name__ == '__main__':
    main()
