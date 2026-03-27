import os

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/version")
def version() -> dict:
    """Return build metadata. Used by the deploy workflow to verify the running image."""
    return {"git_sha": os.environ.get("APP_GIT_SHA", "unknown")}
