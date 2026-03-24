import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.story import Story
from app.schemas.story import StoryOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stories", tags=["stories"])


@router.get("/", response_model=list[StoryOut])
def list_stories(db: Session = Depends(get_db)) -> list[Story]:
    return db.query(Story).order_by(Story.created_at.desc()).all()


@router.get("/{story_id}", response_model=StoryOut)
def get_story(story_id: uuid.UUID, db: Session = Depends(get_db)) -> Story:
    story = db.get(Story, story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story
