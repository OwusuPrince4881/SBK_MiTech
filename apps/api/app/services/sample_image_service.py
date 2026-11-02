from datetime import datetime, timezone
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.domain.sample_image import SampleImage
from app.persistence.sample_image_repository import save_sample_image
from app.persistence.sample_repository import get_sample
from app.schemas.sample_image import SampleImageResponse
from app.settings.uploads import UploadSettings
from app.storage.image_storage import store_image


def register_sample_image(
    session: Session,
    sample_id: UUID,
    source: BinaryIO,
    original_filename: str,
    declared_mime_type: str | None,
    settings: UploadSettings,
) -> SampleImageResponse | None:
    if get_sample(session, sample_id) is None:
        return None

    (
        stored_filename,
        safe_original_filename,
        file_size,
        storage_path,
        mime_type,
    ) = store_image(
        source,
        sample_id,
        original_filename,
        declared_mime_type,
        settings,
    )
    image = SampleImage(
        id=uuid4(),
        sample_id=sample_id,
        stored_filename=stored_filename,
        original_filename=safe_original_filename,
        mime_type=mime_type,
        file_size=file_size,
        uploaded_at=datetime.now(timezone.utc),
        storage_path=storage_path,
    )
    try:
        saved_image = save_sample_image(session, image)
    except Exception:
        (settings.directory / storage_path).unlink(missing_ok=True)
        raise

    return SampleImageResponse(
        id=saved_image.id,
        sample_id=saved_image.sample_id,
        stored_filename=saved_image.stored_filename,
        original_filename=saved_image.original_filename,
        mime_type=saved_image.mime_type,
        file_size=saved_image.file_size,
        uploaded_at=saved_image.uploaded_at,
        storage_path=saved_image.storage_path,
    )
