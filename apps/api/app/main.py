from fastapi import FastAPI

APPLICATION_NAME = "MineMat AI API"
VERSION = "0.1.0"

app = FastAPI(
    title=APPLICATION_NAME,
    description="Backend API for preliminary assessment of mine construction materials.",
    version=VERSION,
)


@app.get("/")
def read_root() -> dict[str, str]:
    return {
        "message": f"Welcome to {APPLICATION_NAME}.",
        "documentation": "/docs",
    }


@app.get("/health")
def read_health() -> dict[str, str]:
    return {
        "status": "healthy",
        "application": APPLICATION_NAME,
        "version": VERSION,
    }
