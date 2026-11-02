from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint_returns_application_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": "MineMat AI API",
        "version": "0.1.0",
    }


def test_root_endpoint_links_to_api_documentation() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to MineMat AI API.",
        "documentation": "/docs",
    }


def test_openapi_metadata_matches_application_details() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"] == {
        "title": "MineMat AI API",
        "description": (
            "Backend API for preliminary assessment of mine construction materials."
        ),
        "version": "0.1.0",
    }
