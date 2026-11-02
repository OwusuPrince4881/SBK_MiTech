from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.persistence.database import get_db
from app.schemas.sample import SampleCreate, SampleResponse
from app.services.sample_service import (
    register_sample,
    retrieve_sample,
    retrieve_samples,
)

router = APIRouter(prefix="/api/v1/samples", tags=["samples"])

@router.post(
    "",
    response_model=SampleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sample(
    sample_data: SampleCreate,
    session: Session = Depends(get_db),
) -> SampleResponse:
    return register_sample(session, sample_data)


@router.get("", response_model=list[SampleResponse])
def read_samples(session: Session = Depends(get_db)) -> list[SampleResponse]:
    return retrieve_samples(session)


@router.get("/{sample_id}", response_model=SampleResponse)
def read_sample(
    sample_id: UUID,
    session: Session = Depends(get_db),
) -> SampleResponse:
    sample = retrieve_sample(session, sample_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sample not found",
        )
    return sample
