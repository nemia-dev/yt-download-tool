# Standard Library
from typing import NotRequired
from typing import TypedDict


class VideoData(TypedDict):
    id: NotRequired[str | None]
    title: str
    is_playlist: bool
    entries: list | None
