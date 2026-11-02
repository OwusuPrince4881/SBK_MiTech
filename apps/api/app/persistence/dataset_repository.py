from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.dataset import Dataset
from app.schemas.dataset import DatasetCreate


def has_duplicate_dataset(session: Session, dataset_data: DatasetCreate) -> bool:
    duplicate_conditions = [
        (Dataset.source_name == dataset_data.source_name)
        & (Dataset.dataset_name == dataset_data.dataset_name)
    ]
    if dataset_data.source_url is not None:
        duplicate_conditions.append(Dataset.source_url == dataset_data.source_url)
    if dataset_data.doi is not None:
        duplicate_conditions.append(Dataset.doi == dataset_data.doi)

    statement = select(Dataset.dataset_id).where(or_(*duplicate_conditions)).limit(1)
    return session.scalar(statement) is not None


def save_dataset(session: Session, dataset: Dataset) -> Dataset:
    session.add(dataset)
    session.commit()
    session.refresh(dataset)
    return dataset


def list_datasets(session: Session) -> list[Dataset]:
    statement = select(Dataset).order_by(Dataset.created_at, Dataset.dataset_id)
    return list(session.scalars(statement).all())


def get_dataset(session: Session, dataset_id: UUID) -> Dataset | None:
    return session.get(Dataset, dataset_id)
