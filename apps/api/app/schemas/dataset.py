from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DatasetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    dataset_name: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    doi: str | None = Field(default=None, min_length=1, max_length=255)
    licence: str = Field(min_length=1, max_length=255)
    acquisition_date: date | None = None
    description: str | None = Field(default=None, max_length=4000)
    known_limitations: str | None = Field(default=None, max_length=4000)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed_url = urlsplit(value)
            has_valid_host = parsed_url.hostname is not None
            parsed_url.port
        except ValueError as error:
            raise ValueError("source_url must be a valid HTTP or HTTPS URL") from error
        if parsed_url.scheme not in {"http", "https"} or not has_valid_host:
            raise ValueError("source_url must be a valid HTTP or HTTPS URL")
        return value

    @field_validator("doi")
    @classmethod
    def normalize_doi(cls, value: str | None) -> str | None:
        return value.casefold() if value is not None else None


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dataset_id: UUID
    dataset_name: str
    source_name: str
    source_url: str | None
    doi: str | None
    licence: str
    acquisition_date: date | None
    description: str | None
    known_limitations: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def ensure_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
