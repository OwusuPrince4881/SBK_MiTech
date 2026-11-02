from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.persistence.database import get_db
from app.schemas.sample_image import SampleImageResponse
from app.services.sample_image_service import register_sample_image
from app.settings.uploads import UploadSettings, get_upload_settings
from app.storage.image_storage import ImageTooLargeError, UnsupportedImageFormatError

router = APIRouter(prefix="/api/v1/samples", tags=["sample images"])


@router.post(
    "/{sample_id}/images",
    response_model=SampleImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_sample_image(
    sample_id: UUID,
    image: UploadFile = File(...),
    session: Session = Depends(get_db),
    settings: UploadSettings = Depends(get_upload_settings),
) -> SampleImageResponse:
    try:
        result = register_sample_image(
            session=session,
            sample_id=sample_id,
            source=image.file,
            original_filename=image.filename or "upload",
            declared_mime_type=image.content_type,
            settings=settings,
        )
    except UnsupportedImageFormatError as error:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(error),
        ) from error
    except ImageTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sample not found",
        )
    return result
