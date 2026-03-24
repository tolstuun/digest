"""
Editorial scoring service.

Combines rule-based pre-score with LLM editorial judgment into a final score,
then upserts one EventClusterAssessment row per cluster.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.scoring.llm import assess_cluster_llm
from app.scoring.rules import compute_rule_score
from app.scoring.schemas import ClusterInput

logger = logging.getLogger(__name__)

# Final score = weighted combination of rule and LLM scores.
# Rule score provides a stable deterministic floor; LLM provides editorial judgment.
_RULE_WEIGHT = 0.4
_LLM_WEIGHT = 0.6


def assess_cluster(
    db: Session, cluster: EventCluster
) -> tuple[EventClusterAssessment, bool]:
    """
    Run full assessment for an event cluster and upsert the result.

    Steps:
      1. Load linked stories and representative story facts.
      2. Compute rule_score deterministically.
      3. Call assess_cluster_llm() for editorial judgment.
      4. Combine: final_score = 0.4 * rule_score + 0.6 * llm_score.
      5. Upsert to event_cluster_assessments (one row per cluster).

    Returns (EventClusterAssessment, created).
    Idempotent: repeated calls update the existing row.
    """
    # 1. Load linked stories
    stories = db.query(Story).filter_by(event_cluster_id=cluster.id).all()
    story_count = len(stories)

    # 2. Load representative story and its facts
    rep_facts: StoryFacts | None = None
    rep_story: Story | None = None

    if cluster.representative_story_id:
        rep_story = db.get(Story, cluster.representative_story_id)
        rep_facts = (
            db.query(StoryFacts)
            .filter_by(story_id=cluster.representative_story_id)
            .first()
        )

    # Fall back to first story with facts if representative has none
    if rep_facts is None:
        for s in stories:
            f = db.query(StoryFacts).filter_by(story_id=s.id).first()
            if f:
                rep_facts = f
                rep_story = s
                break

    # 3. Max source priority across linked stories
    max_priority = 0
    if stories:
        source_ids = [s.source_id for s in stories]
        result = (
            db.query(func.max(Source.priority))
            .filter(Source.id.in_(source_ids))
            .scalar()
        )
        max_priority = int(result or 0)

    # 4. Compute rule score
    has_amount = bool(rep_facts and rep_facts.amount_text)
    has_currency = bool(rep_facts and rep_facts.currency)
    rule_score = compute_rule_score(
        event_type=cluster.event_type or "",
        story_count=story_count,
        has_amount=has_amount,
        has_currency=has_currency,
        max_source_priority=max_priority,
    )

    # 5. Build LLM input
    cluster_input = ClusterInput(
        cluster_id=str(cluster.id),
        event_type=cluster.event_type,
        story_count=story_count,
        company_names=(rep_facts.company_names or []) if rep_facts else [],
        amount_text=rep_facts.amount_text if rep_facts else None,
        currency=rep_facts.currency if rep_facts else None,
        canonical_summary_en=rep_facts.canonical_summary_en if rep_facts else None,
        canonical_summary_ru=rep_facts.canonical_summary_ru if rep_facts else None,
        representative_title=rep_story.title if rep_story else None,
    )

    # 6. LLM editorial assessment
    llm_result = assess_cluster_llm(cluster_input)

    # 7. Final score
    final_score = round(_RULE_WEIGHT * rule_score + _LLM_WEIGHT * llm_result.llm_score, 4)

    # 8. Upsert
    existing = (
        db.query(EventClusterAssessment)
        .filter_by(event_cluster_id=cluster.id)
        .first()
    )
    created = existing is None

    if existing is None:
        assessment = EventClusterAssessment(event_cluster_id=cluster.id)
        db.add(assessment)
    else:
        assessment = existing

    assessment.primary_section = llm_result.primary_section
    assessment.rule_score = rule_score
    assessment.llm_score = llm_result.llm_score
    assessment.final_score = final_score
    assessment.include_in_digest = llm_result.include_in_digest
    assessment.why_it_matters_en = llm_result.why_it_matters_en
    assessment.why_it_matters_ru = llm_result.why_it_matters_ru
    assessment.editorial_notes = llm_result.editorial_notes
    assessment.model_name = settings.extraction_model
    assessment.raw_model_output = llm_result.model_dump()
    assessment.assessed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(assessment)

    logger.info(
        "assess_cluster cluster=%s rule=%.3f llm=%.3f final=%.3f include=%s created=%s",
        cluster.id,
        rule_score,
        llm_result.llm_score,
        final_score,
        llm_result.include_in_digest,
        created,
    )
    return assessment, created
