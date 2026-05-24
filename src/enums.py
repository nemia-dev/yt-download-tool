# Standard Library
from enum import StrEnum
from enum import unique


@unique
class DownloadMode(StrEnum):
    VIDEO = 'video'
    AUDIO = 'audio'
