# Standard Library
from typing import TypedDict


class VideoData(TypedDict):
    id: str
    title: str
    is_playlist: bool
    entries: list | None
