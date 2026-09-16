"""Optional quality information on raw measurement samples."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class SampleQuality(BaseModel):
    """Reset marks the first reading after a reset; generation identifies a meter."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["measured", "estimated", "invalid", "unavailable"] = "measured"
    reset: bool = False
    generation: str | None = Field(default=None, max_length=128)


class MeasurementSample(BaseModel):
    """Complete replacement of one key/timestamp, including its quality."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    date_time: AwareDatetime
    key: str
    value: float | None = Field(strict=True)
    quality: SampleQuality = Field(default_factory=SampleQuality)
