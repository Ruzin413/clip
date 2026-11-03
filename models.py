from pydantic import BaseModel, Field, model_validator


class VideoInfo(BaseModel):
    video_id: str
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


class ClipRequest(BaseModel):
    video_id: str
    start_time: float = Field(ge=0.0)
    end_time: float = Field(ge=0.0)
    filename: str | None = None

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
