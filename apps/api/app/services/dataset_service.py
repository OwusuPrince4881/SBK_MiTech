from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.dataset import Dataset
from app.persistence.dataset_repository import (
    get_dataset,
    has_duplicate_dataset,
    list_datasets,
    save_dataset,
)
from app.schemas.dataset import DatasetCreate, DatasetResponse


class DuplicateDatasetError(ValueError):
    pass


def register_dataset(
    session: Session,
    dataset_data: DatasetCreate,
) -> DatasetResponse:
    if has_duplicate_dataset(session, dataset_data):
        raise DuplicateDatasetError("Dataset is already registered")

    dataset = Dataset(dataset_id=uuid4(), **dataset_data.model_dump())
    try:
        saved_dataset = save_dataset(session, dataset)
    except IntegrityError as error:
        session.rollback()
        raise DuplicateDatasetError("Dataset is already registered") from error
    return DatasetResponse.model_validate(saved_dataset)


def retrieve_datasets(session: Session) -> list[DatasetResponse]:
    return [
        DatasetResponse.model_validate(dataset)
        for dataset in list_datasets(session)
    ]


def retrieve_dataset(
    session: Session,
    dataset_id: UUID,
) -> DatasetResponse | None:
    dataset = get_dataset(session, dataset_id)
    return DatasetResponse.model_validate(dataset) if dataset is not None else None
