from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

MEDIA_KEY_PATTERN = r"^(youtube|twitch|kick)_[A-Za-z0-9_-]+$"


class Platform(StrEnum):
    YOUTUBE = "youtube"
    TWITCH = "twitch"
    KICK = "kick"


class AIProvider(StrEnum):
    AUTO = "auto"
    CLAUDE = "claude"
    GEMINI = "gemini"
    OPENAI = "openai"
    LOCAL = "local"


class VideoInfo(BaseModel):
    video_id: str
    platform: Platform
    title: str
    duration: float = Field(ge=0.0)
    thumbnail: str
    stream_url: str | None = None
    local_path: str | None = None


class CaptionSegment(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str

    @model_validator(mode="after")
    def validate_endpoints(self) -> "CaptionSegment":
        if self.end < self.start:
            raise ValueError("end time cannot be earlier than start time")
        return self


class HighlightClip(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    title: str
    reason: str
    score: float = Field(ge=0.0, le=1.0, default=1.0)

    @model_validator(mode="after")
    def validate_endpoints(self) -> "HighlightClip":
        if self.end <= self.start:
            raise ValueError("end time must be strictly greater than start time")
        return self


class LineSpan(BaseModel):
    """A highlight as an AI model reports it: inclusive indices into the numbered transcript."""
    start_line: int
    end_line: int
    title: str
    reason: str
    score: float


class HighlightPlan(BaseModel):
    highlights: list[LineSpan]


class ProviderSettings(BaseModel):
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


class ProviderInfo(BaseModel):
    id: AIProvider
    label: str
    has_server_key: bool
    needs_key: bool
    accepts_key: bool = False
    saved_key_hint: str | None = None
    has_saved_settings: bool = False
    default_model: str | None = None
    uses_base_url: bool = False
    default_base_url: str | None = None


class ClipRequest(BaseModel):
    video_id: str = Field(pattern=MEDIA_KEY_PATTERN)
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    filename: str | None = Field(default=None, pattern=r"^[\w.-]+$")

    @model_validator(mode="after")
    def validate_bounds(self) -> "ClipRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly greater than start_time")
        return self


class ClipResponse(BaseModel):
    clip_id: str
    file_path: str
    download_url: str
    duration: float = Field(ge=0.0)
