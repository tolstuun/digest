import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_step import PipelineRunStep
from app.schemas.pipeline_run import PipelineRunDetail, PipelineRunOut

router = APIRouter(prefix="/pipeline-runs", tags=["pipeline-runs"])


@router.get("/", response_model=List[PipelineRunOut])
def list_pipeline_runs(db: Session = Depends(get_db)) -> List[PipelineRun]:
    """List all pipeline runs, most recent first."""
    return (
        db.query(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .all()
    )


@router.get("/{run_id}", response_model=PipelineRunDetail)
def get_pipeline_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> PipelineRunDetail:
    """Get a pipeline run with all its steps in execution order."""
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    steps = (
        db.query(PipelineRunStep)
        .filter_by(pipeline_run_id=run_id)
        .order_by(PipelineRunStep.created_at)
        .all()
    )

    detail = PipelineRunDetail.model_validate(run)
    detail.steps = [s for s in steps]
    return detail
