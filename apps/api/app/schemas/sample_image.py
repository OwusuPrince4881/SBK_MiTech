from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, field_validator


class SampleImageResponse(BaseModel):
    id: UUID
    sample_id: UUID
    stored_filename: str
    original_filename: str
    mime_type: str
    file_size: int
    uploaded_at: datetime
    storage_path: str

    @field_validator("uploaded_at")
    @classmethod
    def ensure_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
