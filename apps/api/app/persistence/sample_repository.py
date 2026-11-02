from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.sample import Sample


def save_sample(session: Session, sample: Sample) -> Sample:
    session.add(sample)
    session.commit()
    session.refresh(sample)
    return sample


def list_samples(session: Session) -> list[Sample]:
    statement = select(Sample).order_by(Sample.created_at, Sample.id)
    return list(session.scalars(statement).all())


def get_sample(session: Session, sample_id: UUID) -> Sample | None:
    return session.get(Sample, sample_id)
