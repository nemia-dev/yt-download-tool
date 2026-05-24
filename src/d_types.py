# Standard Library
from typing import TypedDict


class VideoData(TypedDict):
    title: str
    is_playlist: bool
    entries: list | None
