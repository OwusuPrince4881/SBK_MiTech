import os
from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def get_database_url() -> str:
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url

    database_path = PROJECT_ROOT / "data" / "minemat_ai.db"
    return URL.create("sqlite", database=str(database_path)).render_as_string(
        hide_password=False
    )


def create_database_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (
        None,
        "",
        ":memory:",
    ):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    connect_args = (
        {"check_same_thread": False} if url.get_backend_name() == "sqlite" else {}
    )
    return create_engine(url, connect_args=connect_args)


def get_db(request: Request) -> Generator[Session, None, None]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
