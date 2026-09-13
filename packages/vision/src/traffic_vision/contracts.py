from typing import Protocol

from pydantic import Field, model_validator
from traffic_core.models import Direction, Model, ObjectKind


class BoundingBox(Model):
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)
    x2: float = Field(ge=0)
    y2: float = Field(ge=0)

    @model_validator(mode="after")
    def positive_area(self) -> "BoundingBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("Bounding box must have positive area")
        return self


class TrackedObject(Model):
    track_id: int = Field(ge=0)
    kind: ObjectKind
    confidence: float = Field(ge=0, le=1)
    box: BoundingBox
    direction: Direction | None = None


class TrackedFrame(Model):
    source_id: str
    timestamp_seconds: float = Field(ge=0)
    objects: tuple[TrackedObject, ...]


class TrackingSource(Protocol):
    """Camera adapters emit observations, never hardware commands."""

    async def read(self) -> TrackedFrame | None: ...

    async def close(self) -> None: ...
