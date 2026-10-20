# Standard Library
from dataclasses import dataclass
from pathlib import Path

# 3rd-Party
from environs import Env

env = Env()
env.read_env()


@dataclass(frozen=True)
class AppConfig:
    max_concurrent_downloads: int
    root_folder: Path
    video_folder: Path
    audio_folder: Path
    transcript_folder: Path


def load_config() -> AppConfig:
    root_folder = Path(env.str('DEFAULT_ROOT_FOLDER', '.')).expanduser()

    return AppConfig(
        max_concurrent_downloads=env.int('MAX_CONCURRENT_DOWNLOADS', 3),
        root_folder=root_folder,
        video_folder=root_folder / env.str('DEFAULT_VIDEO_FOLDER', 'Video'),
        audio_folder=root_folder / env.str('DEFAULT_AUDIO_FOLDER', 'Audio'),
        transcript_folder=root_folder / env.str('DEFAULT_TRANS_FOLDER', 'Transcription'),
    )
