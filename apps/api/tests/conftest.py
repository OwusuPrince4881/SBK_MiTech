from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    database_url = URL.create(
        "sqlite",
        database=str(tmp_path / "test.db"),
    ).render_as_string(hide_password=False)
    test_app = create_app(database_url=database_url)

    with TestClient(test_app) as test_client:
        yield test_client
