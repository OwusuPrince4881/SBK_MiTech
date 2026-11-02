from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.api.samples import router as samples_router
from app.domain.base import Base
from app.persistence.database import create_database_engine, get_database_url

APPLICATION_NAME = "MineMat AI API"
VERSION = "0.1.0"


def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(database_url or get_database_url())
        Base.metadata.create_all(engine)
        app.state.session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title=APPLICATION_NAME,
        description=(
            "Backend API for preliminary assessment of mine construction materials."
        ),
        version=VERSION,
        lifespan=lifespan,
    )
    app.include_router(samples_router)
    app.add_api_route("/", read_root, methods=["GET"])
    app.add_api_route("/health", read_health, methods=["GET"])
    return app


def read_root() -> dict[str, str]:
    return {
        "message": f"Welcome to {APPLICATION_NAME}.",
        "documentation": "/docs",
    }


def read_health() -> dict[str, str]:
    return {
        "status": "healthy",
        "application": APPLICATION_NAME,
        "version": VERSION,
    }


app = create_app()
