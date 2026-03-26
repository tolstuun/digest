import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import settings
from app.routers import admin, digest_pages, digest_publications, digests, event_clusters, health, llm_usages, pipeline_runs, sources, stories, ui
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Security Digest API starting up")
    start_scheduler(settings)
    yield
    stop_scheduler()
    logger.info("Security Digest API shut down")


app = FastAPI(title="Security Digest API", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(stories.router)
app.include_router(event_clusters.router)
app.include_router(digests.router)
app.include_router(digest_pages.router)
app.include_router(digest_publications.router)
app.include_router(pipeline_runs.router)
app.include_router(llm_usages.router)
app.include_router(admin.router)
app.include_router(ui.router)
