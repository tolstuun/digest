import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.routers import admin, digest_pages, digests, event_clusters, health, sources, stories, ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Security Digest API starting up")
    yield


app = FastAPI(title="Security Digest API", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(stories.router)
app.include_router(event_clusters.router)
app.include_router(digests.router)
app.include_router(digest_pages.router)
app.include_router(admin.router)
app.include_router(ui.router)
