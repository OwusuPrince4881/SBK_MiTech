from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.sample import Sample

SAMPLES_URL = "/api/v1/samples"

# Initial application input limits (not engineering standards).
SAMPLE_FIELD_LIMITS = {
    "sample_name": 200,
    "collection_location": 200,
    "material_description": 2000,
    "intended_application": 2000,
}
SAMPLE_RESPONSE_KEYS = {
    "id",
    "sample_name",
    "material_description",
    "collection_location",
    "intended_application",
    "registered_at",
}


def sample_payload() -> dict[str, str]:
    return {
        "sample_name": "North pit sample 01",
        "material_description": "Soil and aggregate sample",
        "collection_location": "North pit",
        "intended_application": "Road construction",
    }


def count_samples(client: TestClient) -> int:
    with client.app.state.session_factory() as session:
        return session.scalar(select(func.count()).select_from(Sample))


def insert_sample(client: TestClient, **overrides: object) -> None:
    """Insert a row directly, bypassing the API (and so its input validation)."""
    values: dict[str, object] = {
        "id": uuid4(),
        "sample_name": "Synthetic sample",
        "material_description": "Synthetic material",
        "collection_location": "Synthetic location",
        "intended_application": "Synthetic application",
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    with client.app.state.session_factory() as session:
        session.add(Sample(**values))
        session.commit()


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


@pytest.mark.parametrize(
    "unknown_field",
    ["cbr", "id", "registered_at", "created_at", "images"],
)
def test_register_sample_rejects_unknown_fields(
    client: TestClient,
    unknown_field: str,
) -> None:
    response = client.post(
        SAMPLES_URL,
        json={**sample_payload(), unknown_field: "unexpected"},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert len(errors) == 1
    assert errors[0]["type"] == "extra_forbidden"
    assert errors[0]["loc"] == ["body", unknown_field]
    assert count_samples(client) == 0


@pytest.mark.parametrize(("field", "limit"), SAMPLE_FIELD_LIMITS.items())
def test_register_sample_accepts_value_at_maximum_length(
    client: TestClient,
    field: str,
    limit: int,
) -> None:
    value = "x" * limit

    response = client.post(SAMPLES_URL, json={**sample_payload(), field: value})

    assert response.status_code == 201
    assert response.json()[field] == value
    stored = client.get(f"{SAMPLES_URL}/{response.json()['id']}")
    assert stored.json()[field] == value


@pytest.mark.parametrize(("field", "limit"), SAMPLE_FIELD_LIMITS.items())
def test_register_sample_rejects_value_one_character_over_maximum(
    client: TestClient,
    field: str,
    limit: int,
) -> None:
    response = client.post(
        SAMPLES_URL,
        json={**sample_payload(), field: "x" * (limit + 1)},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert len(errors) == 1
    assert errors[0]["type"] == "string_too_long"
    assert errors[0]["loc"] == ["body", field]
    assert errors[0]["ctx"] == {"max_length": limit}
    assert count_samples(client) == 0


@pytest.mark.parametrize("field", SAMPLE_FIELD_LIMITS)
@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n  "])
def test_register_sample_rejects_blank_required_values(
    client: TestClient,
    field: str,
    blank_value: str,
) -> None:
    response = client.post(
        SAMPLES_URL,
        json={**sample_payload(), field: blank_value},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert len(errors) == 1
    assert errors[0]["type"] == "string_too_short"
    assert errors[0]["loc"] == ["body", field]
    assert count_samples(client) == 0


def test_register_sample_trims_surrounding_whitespace_but_not_interior(
    client: TestClient,
) -> None:
    payload = {**sample_payload(), "material_description": "Line one\n\nLine  two"}
    padded = {field: f"  \t{value}\n " for field, value in payload.items()}

    response = client.post(SAMPLES_URL, json=padded)

    assert response.status_code == 201
    for field, value in payload.items():
        assert response.json()[field] == value
    with client.app.state.session_factory() as session:
        stored = session.scalars(select(Sample)).one()
    for field, value in payload.items():
        assert getattr(stored, field) == value


@pytest.mark.parametrize(("field", "limit"), SAMPLE_FIELD_LIMITS.items())
def test_length_limit_is_applied_after_trimming(
    client: TestClient,
    field: str,
    limit: int,
) -> None:
    value = "x" * limit

    response = client.post(
        SAMPLES_URL,
        json={**sample_payload(), field: f"  {value}\n"},
    )

    assert response.status_code == 201
    assert response.json()[field] == value


@pytest.mark.parametrize(
    "invalid_body",
    [
        pytest.param({"sample_name": None}, id="null-value"),
        pytest.param({"intended_application": 42}, id="number-value"),
        pytest.param({"collection_location": ["North pit"]}, id="list-value"),
    ],
)
def test_invalid_field_values_do_not_create_records(
    client: TestClient,
    invalid_body: dict[str, object],
) -> None:
    response = client.post(SAMPLES_URL, json={**sample_payload(), **invalid_body})

    assert response.status_code == 422
    assert count_samples(client) == 0


@pytest.mark.parametrize("body", [[], "North pit sample", None])
def test_non_object_request_bodies_do_not_create_records(
    client: TestClient,
    body: object,
) -> None:
    response = client.post(SAMPLES_URL, json=body)

    assert response.status_code == 422
    assert count_samples(client) == 0


def test_a_rejected_submission_does_not_block_a_later_valid_one(
    client: TestClient,
) -> None:
    rejected = client.post(SAMPLES_URL, json={**sample_payload(), "cbr": 80})
    accepted = client.post(SAMPLES_URL, json=sample_payload())

    assert rejected.status_code == 422
    assert accepted.status_code == 201
    assert count_samples(client) == 1


def test_sample_responses_keep_the_documented_keys(client: TestClient) -> None:
    created = client.post(SAMPLES_URL, json=sample_payload())
    sample_id = created.json()["id"]

    listed = client.get(SAMPLES_URL)
    fetched = client.get(f"{SAMPLES_URL}/{sample_id}")

    assert set(created.json()) == SAMPLE_RESPONSE_KEYS
    assert [set(item) for item in listed.json()] == [SAMPLE_RESPONSE_KEYS]
    assert set(fetched.json()) == SAMPLE_RESPONSE_KEYS


def test_list_samples_is_ordered_by_registration_time(client: TestClient) -> None:
    base = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    insert_sample(client, sample_name="newest", created_at=base + timedelta(hours=2))
    insert_sample(client, sample_name="oldest", created_at=base)
    insert_sample(client, sample_name="middle", created_at=base + timedelta(hours=1))

    response = client.get(SAMPLES_URL)

    assert response.status_code == 200
    assert [item["sample_name"] for item in response.json()] == [
        "oldest",
        "middle",
        "newest",
    ]


def test_list_samples_breaks_registration_time_ties_by_id(
    client: TestClient,
) -> None:
    tied_time = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    insert_sample(client, id=UUID(int=2), sample_name="second", created_at=tied_time)
    insert_sample(client, id=UUID(int=1), sample_name="first", created_at=tied_time)

    response = client.get(SAMPLES_URL)

    assert [item["sample_name"] for item in response.json()] == ["first", "second"]


def test_existing_records_beyond_the_new_limits_remain_readable(
    client: TestClient,
) -> None:
    legacy_id = uuid4()
    insert_sample(
        client,
        id=legacy_id,
        sample_name="L" * 500,
        collection_location="C" * 500,
        material_description="M" * 5000,
        intended_application="A" * 5000,
    )

    listed = client.get(SAMPLES_URL)
    fetched = client.get(f"{SAMPLES_URL}/{legacy_id}")

    assert listed.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["sample_name"] == "L" * 500
    assert fetched.json()["material_description"] == "M" * 5000
    assert listed.json() == [fetched.json()]
