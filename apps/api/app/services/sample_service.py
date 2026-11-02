from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.domain.sample import Sample
from app.persistence.sample_repository import (
    get_sample,
    list_samples,
    save_sample,
)
from app.schemas.sample import SampleCreate, SampleResponse


def register_sample(session: Session, sample_data: SampleCreate) -> SampleResponse:
    sample = Sample(
        id=uuid4(),
        created_at=datetime.now(timezone.utc),
        **sample_data.model_dump(),
    )
    return _to_response(save_sample(session, sample))


def retrieve_samples(session: Session) -> list[SampleResponse]:
    return [_to_response(sample) for sample in list_samples(session)]


def retrieve_sample(session: Session, sample_id: UUID) -> SampleResponse | None:
    sample = get_sample(session, sample_id)
    return _to_response(sample) if sample is not None else None


def _to_response(sample: Sample) -> SampleResponse:
    return SampleResponse(
        id=sample.id,
        sample_name=sample.sample_name,
        material_description=sample.material_description,
        collection_location=sample.collection_location,
        intended_application=sample.intended_application,
        registered_at=sample.created_at,
    )
