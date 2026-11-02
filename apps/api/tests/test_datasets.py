from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain.dataset import Dataset

DATASETS_URL = "/api/v1/datasets"


def dataset_payload() -> dict[str, object]:
    return {
        "dataset_name": "Aggregate Image Collection",
        "source_name": "Example Research Group",
        "source_url": "https://example.invalid/aggregate-images",
        "doi": "10.0000/example.dataset",
        "licence": "Fictional placeholder licence",
        "acquisition_date": "2025-02-14",
        "description": "A test-only dataset record.",
        "known_limitations": "No real dataset is represented.",
    }


def test_register_dataset_returns_created_dataset(client: TestClient) -> None:
    response = client.post(DATASETS_URL, json=dataset_payload())

    assert response.status_code == 201
    registered = response.json()
    assert UUID(registered["dataset_id"])
    assert registered["dataset_name"] == "Aggregate Image Collection"
    assert registered["source_name"] == "Example Research Group"
    assert registered["source_url"] == "https://example.invalid/aggregate-images"
    assert registered["doi"] == "10.0000/example.dataset"
    assert registered["licence"] == "Fictional placeholder licence"
    assert registered["acquisition_date"] == "2025-02-14"
    assert registered["description"] == "A test-only dataset record."
    assert registered["known_limitations"] == "No real dataset is represented."
    assert registered["created_at"]
    assert registered["updated_at"]


@pytest.mark.parametrize(
    "missing_field",
    ["dataset_name", "source_name", "licence"],
)
def test_register_dataset_rejects_missing_required_fields(
    client: TestClient,
    missing_field: str,
) -> None:
    payload = dataset_payload()
    del payload[missing_field]

    response = client.post(DATASETS_URL, json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"dataset_name": "   "},
        {"source_name": "X" * 201},
        {"source_url": "ftp://example.invalid/dataset"},
        {"source_url": "https://"},
        {"acquisition_date": "not-a-date"},
        {"licence": ""},
    ],
)
def test_register_dataset_rejects_invalid_inputs(
    client: TestClient,
    invalid_fields: dict[str, object],
) -> None:
    response = client.post(
        DATASETS_URL,
        json={**dataset_payload(), **invalid_fields},
    )

    assert response.status_code == 422


def test_list_datasets(client: TestClient) -> None:
    created = client.post(DATASETS_URL, json=dataset_payload())

    response = client.get(DATASETS_URL)

    assert response.status_code == 200
    assert [item["dataset_id"] for item in response.json()] == [
        created.json()["dataset_id"]
    ]


def test_retrieve_dataset_by_id(client: TestClient) -> None:
    created = client.post(DATASETS_URL, json=dataset_payload())

    response = client.get(f"{DATASETS_URL}/{created.json()['dataset_id']}")

    assert response.status_code == 200
    assert response.json() == created.json()


def test_retrieve_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.get(f"{DATASETS_URL}/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_duplicate_dataset_registration_returns_409(client: TestClient) -> None:
    first = client.post(DATASETS_URL, json=dataset_payload())
    second_payload = {
        **dataset_payload(),
        "dataset_name": "Renamed listing",
        "source_name": "Another source label",
        "doi": "10.0000/EXAMPLE.DATASET",
    }

    duplicate = client.post(DATASETS_URL, json=second_payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Dataset is already registered"}


def test_duplicate_dataset_source_url_returns_409(client: TestClient) -> None:
    first = client.post(DATASETS_URL, json=dataset_payload())
    second_payload = {
        **dataset_payload(),
        "dataset_name": "A different listing",
        "source_name": "A different source",
        "doi": "10.0000/a-different-dataset",
    }

    duplicate = client.post(DATASETS_URL, json=second_payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_dataset_persists_across_database_sessions(client: TestClient) -> None:
    created = client.post(DATASETS_URL, json=dataset_payload())
    dataset_id = UUID(created.json()["dataset_id"])

    with client.app.state.session_factory() as session:
        persisted = session.get(Dataset, dataset_id)

    assert persisted is not None
    assert persisted.dataset_name == "Aggregate Image Collection"
    assert persisted.source_name == "Example Research Group"
    assert persisted.acquisition_date == date(2025, 2, 14)


def test_duplicate_source_and_name_without_external_identifiers_returns_409(
    client: TestClient,
) -> None:
    payload = {
        "dataset_name": "Local Collection",
        "source_name": "Fictional Archive",
        "licence": "Unknown; use not authorized",
    }
    first = client.post(DATASETS_URL, json=payload)

    duplicate = client.post(DATASETS_URL, json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_same_dataset_name_from_different_sources_is_allowed(
    client: TestClient,
) -> None:
    first = client.post(DATASETS_URL, json=dataset_payload())
    second_payload = {
        "dataset_name": dataset_payload()["dataset_name"],
        "source_name": "A different source",
        "licence": "Fictional placeholder licence",
    }

    second = client.post(DATASETS_URL, json=second_payload)

    assert first.status_code == 201
    assert second.status_code == 201
