from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

SAMPLES_URL = "/api/v1/samples"


def sample_payload() -> dict[str, str]:
    return {
        "sample_name": "North pit sample 01",
        "material_description": "Soil and aggregate sample",
        "collection_location": "North pit",
        "intended_application": "Road construction",
    }


def test_register_sample_returns_sample_with_uuid_and_utc_timestamp(
    client: TestClient,
) -> None:
    response = client.post(SAMPLES_URL, json=sample_payload())

    assert response.status_code == 201
    registered_sample = response.json()
    assert UUID(registered_sample["id"])
    assert registered_sample["sample_name"] == "North pit sample 01"
    assert registered_sample["material_description"] == "Soil and aggregate sample"
    assert registered_sample["collection_location"] == "North pit"
    assert registered_sample["intended_application"] == "Road construction"
    registered_at = datetime.fromisoformat(registered_sample["registered_at"])
    assert registered_at.tzinfo == timezone.utc


def test_register_sample_rejects_missing_required_fields(client: TestClient) -> None:
    response = client.post(
        SAMPLES_URL,
        json={"sample_name": "North pit sample 01"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_value",
    [
        {"sample_name": "   "},
        {"sample_name": 123},
    ],
)
def test_register_sample_rejects_invalid_input(
    client: TestClient,
    invalid_value: dict[str, object],
) -> None:
    response = client.post(
        SAMPLES_URL,
        json={**sample_payload(), **invalid_value},
    )

    assert response.status_code == 422


def test_register_samples_generate_unique_ids(client: TestClient) -> None:
    first_response = client.post(SAMPLES_URL, json=sample_payload())
    second_response = client.post(
        SAMPLES_URL,
        json={**sample_payload(), "sample_name": "North pit sample 02"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["id"] != second_response.json()["id"]


def test_retrieve_all_samples(client: TestClient) -> None:
    created = client.post(SAMPLES_URL, json=sample_payload())

    response = client.get(SAMPLES_URL)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == created.json()["id"]


def test_retrieve_sample_by_id(client: TestClient) -> None:
    created = client.post(SAMPLES_URL, json=sample_payload())
    sample_id = created.json()["id"]

    response = client.get(f"{SAMPLES_URL}/{sample_id}")

    assert response.status_code == 200
    assert response.json() == created.json()


def test_retrieve_sample_persists_across_database_sessions(
    client: TestClient,
) -> None:
    created = client.post(SAMPLES_URL, json=sample_payload())

    response = client.get(f"{SAMPLES_URL}/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created.json()["id"]


def test_retrieve_nonexistent_sample_returns_404(client: TestClient) -> None:
    response = client.get(f"{SAMPLES_URL}/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Sample not found"}
