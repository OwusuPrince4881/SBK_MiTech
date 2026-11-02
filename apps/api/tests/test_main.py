from fastapi.testclient import TestClient


def test_health_endpoint_returns_application_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": "MineMat AI API",
        "version": "0.1.0",
    }


def test_root_endpoint_links_to_api_documentation(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to MineMat AI API.",
        "documentation": "/docs",
    }


def test_openapi_metadata_matches_application_details(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"] == {
        "title": "MineMat AI API",
        "description": (
            "Backend API for preliminary assessment of mine construction materials."
        ),
        "version": "0.1.0",
    }


def test_openapi_documents_sample_registration_endpoint(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/samples" in response.json()["paths"]
    assert "post" in response.json()["paths"]["/api/v1/samples"]
    assert "get" in response.json()["paths"]["/api/v1/samples"]
    assert "get" in response.json()["paths"]["/api/v1/samples/{sample_id}"]
    assert (
        "post"
        in response.json()["paths"]["/api/v1/samples/{sample_id}/images"]
    )


def test_openapi_documents_dataset_registry_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "post" in response.json()["paths"]["/api/v1/datasets"]
    assert "get" in response.json()["paths"]["/api/v1/datasets"]
    assert "get" in response.json()["paths"]["/api/v1/datasets/{dataset_id}"]
