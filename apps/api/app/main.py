from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app.api.datasets import router as datasets_router
from app.api.samples import router as samples_router
from app.api.sample_images import router as sample_images_router
from app.domain.base import Base
from app.domain.dataset import Dataset
from app.domain.sample_image import SampleImage
from app.persistence.database import create_database_engine, get_database_url
from app.settings.uploads import create_upload_settings

APPLICATION_NAME = "MineMat AI API"
VERSION = "0.1.0"


def create_app(
    database_url: str | None = None,
    upload_directory: Path | None = None,
    max_upload_size_bytes: int | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(database_url or get_database_url())
        Base.metadata.create_all(engine)
        app.state.session_factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )
        app.state.upload_settings = create_upload_settings(
            directory=upload_directory,
            max_file_size_bytes=max_upload_size_bytes,
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
    app.include_router(datasets_router)
    app.include_router(samples_router)
    app.include_router(sample_images_router)
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
