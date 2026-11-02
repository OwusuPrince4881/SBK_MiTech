from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.sample_image import SampleImage

SAMPLES_URL = "/api/v1/samples"


def register_sample(client: TestClient) -> str:
    response = client.post(
        SAMPLES_URL,
        json={
            "sample_name": "North pit sample",
            "material_description": "Soil and aggregate sample",
            "collection_location": "North pit",
            "intended_application": "Road construction",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_upload_image_returns_and_persists_metadata(client: TestClient) -> None:
    sample_id = register_sample(client)
    image_content = b"\xff\xd8\xff" + b"jpeg-image-content"

    response = client.post(
        f"{SAMPLES_URL}/{sample_id}/images",
        files={"image": ("../../unsafe.jpg", image_content, "image/jpeg")},
    )

    assert response.status_code == 201
    metadata = response.json()
    assert UUID(metadata["id"])
    assert metadata["sample_id"] == sample_id
    assert metadata["stored_filename"].endswith(".jpg")
    assert "/" not in metadata["stored_filename"]
    assert "\\" not in metadata["stored_filename"]
    assert metadata["original_filename"] == "unsafe.jpg"
    assert metadata["mime_type"] == "image/jpeg"
    assert metadata["file_size"] == len(image_content)
    assert metadata["storage_path"] == f"{sample_id}/{metadata['stored_filename']}"

    storage_root = client.app.state.upload_settings.directory
    stored_file = storage_root / metadata["storage_path"]
    assert stored_file.is_file()
    assert stored_file.read_bytes() == image_content

    with client.app.state.session_factory() as session:
        persisted = session.scalar(
            select(SampleImage).where(SampleImage.id == UUID(metadata["id"]))
        )
    assert persisted is not None
    assert persisted.image_id == UUID(metadata["id"])
    assert persisted.id == persisted.image_id
    assert persisted.sample_id == UUID(sample_id)
    assert persisted.original_filename == "unsafe.jpg"
    assert persisted.mime_type == "image/jpeg"
    assert persisted.file_size == len(image_content)
    assert persisted.relative_path == metadata["storage_path"]
    assert persisted.storage_path == metadata["storage_path"]
    assert persisted.uploaded_at is not None


def test_upload_multiple_images_for_one_sample(client: TestClient) -> None:
    sample_id = register_sample(client)
    images = [
        ("one.png", b"\x89PNG\r\n\x1a\n" + b"png-content", "image/png"),
        ("two.webp", b"RIFF\x00\x00\x00\x00WEBP" + b"webp-content", "image/webp"),
    ]

    responses = [
        client.post(
            f"{SAMPLES_URL}/{sample_id}/images",
            files={"image": image},
        )
        for image in images
    ]

    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json()["sample_id"] == sample_id
    assert responses[1].json()["sample_id"] == sample_id
    assert responses[0].json()["id"] != responses[1].json()["id"]
    assert responses[0].json()["mime_type"] == "image/png"
    assert responses[1].json()["mime_type"] == "image/webp"


def test_upload_to_nonexistent_sample_returns_404(client: TestClient) -> None:
    response = client.post(
        f"{SAMPLES_URL}/{UUID(int=1)}/images",
        files={"image": ("photo.jpg", b"\xff\xd8\xffcontent", "image/jpeg")},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Sample not found"}
    assert not list(client.app.state.upload_settings.directory.rglob("*"))


@pytest.mark.parametrize(
    ("filename", "content", "mime_type"),
    [
        ("photo.gif", b"GIF89a-image-data", "image/gif"),
        ("photo.jpg", b"\x89PNG\r\n\x1a\nimage-data", "image/jpeg"),
    ],
)
def test_upload_rejects_unsupported_or_mismatched_formats(
    client: TestClient,
    filename: str,
    content: bytes,
    mime_type: str,
) -> None:
    sample_id = register_sample(client)

    response = client.post(
        f"{SAMPLES_URL}/{sample_id}/images",
        files={"image": (filename, content, mime_type)},
    )

    assert response.status_code == 415
    assert not list(client.app.state.upload_settings.directory.rglob("*"))


def test_upload_rejects_oversized_image(client: TestClient) -> None:
    sample_id = register_sample(client)
    content = b"\xff\xd8\xff" + b"x" * 126

    response = client.post(
        f"{SAMPLES_URL}/{sample_id}/images",
        files={"image": ("large.jpg", content, "image/jpeg")},
    )

    assert response.status_code == 413
    assert not list(client.app.state.upload_settings.directory.rglob("*"))
