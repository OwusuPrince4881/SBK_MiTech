from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.persistence.database import get_db
from app.schemas.dataset import DatasetCreate, DatasetResponse
from app.services.dataset_service import (
    DuplicateDatasetError,
    register_dataset,
    retrieve_dataset,
    retrieve_datasets,
)

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


@router.post(
    "",
    response_model=DatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dataset(
    dataset_data: DatasetCreate,
    session: Session = Depends(get_db),
) -> DatasetResponse:
    try:
        return register_dataset(session, dataset_data)
    except DuplicateDatasetError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


@router.get("", response_model=list[DatasetResponse])
def read_datasets(session: Session = Depends(get_db)) -> list[DatasetResponse]:
    return retrieve_datasets(session)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def read_dataset(
    dataset_id: UUID,
    session: Session = Depends(get_db),
) -> DatasetResponse:
    dataset = retrieve_dataset(session, dataset_id)
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    return dataset
