import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from app.persistence.database import PROJECT_ROOT

DEFAULT_MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class UploadSettings:
    directory: Path
    max_file_size_bytes: int


def create_upload_settings(
    directory: Path | None = None,
    max_file_size_bytes: int | None = None,
) -> UploadSettings:
    configured_directory = directory
    if configured_directory is None:
        configured_directory = Path(
            os.getenv("IMAGE_UPLOAD_DIR", str(PROJECT_ROOT / "data" / "uploads"))
        )
    configured_max_size = max_file_size_bytes
    if configured_max_size is None:
        configured_max_size = int(
            os.getenv(
                "MAX_IMAGE_UPLOAD_SIZE_BYTES",
                str(DEFAULT_MAX_IMAGE_SIZE_BYTES),
            )
        )
    if configured_max_size <= 0:
        raise ValueError("Maximum image upload size must be greater than zero")

    return UploadSettings(
        directory=configured_directory.resolve(),
        max_file_size_bytes=configured_max_size,
    )


def get_upload_settings(request: Request) -> UploadSettings:
    return request.app.state.upload_settings
