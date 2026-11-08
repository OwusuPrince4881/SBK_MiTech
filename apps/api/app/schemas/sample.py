from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SampleCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sample_name: str = Field(min_length=1, max_length=200)
    material_description: str = Field(min_length=1, max_length=2000)
    collection_location: str = Field(min_length=1, max_length=200)
    intended_application: str = Field(min_length=1, max_length=2000)


class SampleResponse(BaseModel):
    id: UUID
    sample_name: str
    material_description: str
    collection_location: str
    intended_application: str
    registered_at: datetime

    @field_validator("registered_at")
    @classmethod
    def ensure_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
