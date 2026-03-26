"""
Digest assembly service for the companies_business section.

Candidate selection rules:
  - Only clusters with an EventClusterAssessment present.
  - primary_section must match the target section_name.
  - include_in_digest must be True.
  - Date assignment (for digest_date filtering):
      Primary: representative story's published_at.date() if available.
      Fallback: event_cluster.created_at.date().
  - Sort included candidates by final_score descending.
  - Limit to max_entries (default: 20).

Idempotent policy: delete-and-rebuild.
  If a DigestRun already exists for (digest_date, section_name), it is deleted
  (cascade-deleting all DigestEntry rows), then rebuilt from scratch.
  This keeps the implementation simple and the run state consistent.
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.digest.filters import should_include_in_companies_business
from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts

logger = logging.getLogger(__name__)

# The only section assembled in Phase 4A.
SECTION_NAME = "companies_business"

# Maximum entries per digest run. Explicit in code; can be overridden per call.
MAX_ENTRIES_DEFAULT = 20

# Type alias for readability
_CandidateTuple = tuple[
    EventClusterAssessment,
    EventCluster,
    Optional[Story],
    Optional[StoryFacts],
]


def _effective_date(cluster: EventCluster, rep_story: Optional[Story]) -> date:
    """
    Return the date used for digest_date filtering.

    Primary:  representative story's published_at.date()
    Fallback: event_cluster.created_at.date()
    """
    if rep_story is not None and rep_story.published_at is not None:
        return rep_story.published_at.date()
    return cluster.created_at.date()


def _load_candidates_for_date(
    db: Session,
    digest_date: date,
    section_name: str,
) -> list[_CandidateTuple]:
    """
    Load all assessed clusters that match digest_date and section_name,
    regardless of include_in_digest.

    Returns list of (assessment, cluster, rep_story, rep_facts) tuples.
    Used to compute total_candidate_clusters.
    """
    assessments = (
        db.query(EventClusterAssessment)
        .filter(EventClusterAssessment.primary_section == section_name)
        .all()
    )
    result: list[_CandidateTuple] = []
    for assessment in assessments:
        cluster = db.get(EventCluster, assessment.event_cluster_id)
        if cluster is None:
            continue
        rep_story: Optional[Story] = None
        rep_facts: Optional[StoryFacts] = None
        if cluster.representative_story_id:
            rep_story = db.get(Story, cluster.representative_story_id)
            rep_facts = (
                db.query(StoryFacts)
                .filter_by(story_id=cluster.representative_story_id)
                .first()
            )
        if _effective_date(cluster, rep_story) == digest_date:
            result.append((assessment, cluster, rep_story, rep_facts))
    return result


def assemble_digest(
    db: Session,
    digest_date: date,
    section_name: str = SECTION_NAME,
    max_entries: int = MAX_ENTRIES_DEFAULT,
) -> tuple[DigestRun, list[DigestEntry], bool]:
    """
    Assemble a digest for the given date and section.

    Idempotent: if a run already exists for (digest_date, section_name),
    it is deleted (cascade-deleting all entries) and rebuilt from scratch.

    Returns (digest_run, entries, created_fresh).
    created_fresh=True means no prior run existed; False means it was rebuilt.
    """
    # Idempotent: delete existing run for this date+section
    existing = (
        db.query(DigestRun)
        .filter_by(digest_date=digest_date, section_name=section_name)
        .first()
    )
    was_existing = existing is not None
    if existing is not None:
        db.delete(existing)
        db.flush()

    # Load all candidates for this date+section (for the total_candidate_clusters count)
    all_candidates = _load_candidates_for_date(db, digest_date, section_name)
    total_candidates = len(all_candidates)

    # Filter: include_in_digest=True AND passes relevance gate
    def _passes_relevance(t: _CandidateTuple) -> bool:
        assessment, cluster, rep_story, rep_facts = t
        if not assessment.include_in_digest:
            return False
        if section_name == SECTION_NAME:
            source_name: Optional[str] = None
            if rep_story and rep_story.source_id:
                source = db.get(Source, rep_story.source_id)
                source_name = source.name if source else None
            return should_include_in_companies_business(
                event_type=cluster.event_type,
                title=rep_story.title if rep_story else None,
                summary_en=rep_facts.canonical_summary_en if rep_facts else None,
                company_names=rep_facts.company_names if rep_facts else None,
                source_name=source_name,
            )
        return True

    included = [t for t in all_candidates if _passes_relevance(t)]
    included.sort(key=lambda t: t[0].final_score or 0.0, reverse=True)
    included = included[:max_entries]

    # Create DigestRun
    now = datetime.now(timezone.utc)
    status = "assembled" if included else "empty"
    run = DigestRun(
        digest_date=digest_date,
        section_name=section_name,
        status=status,
        total_candidate_clusters=total_candidates,
        total_included_clusters=len(included),
        generated_at=now,
    )
    db.add(run)
    db.flush()

    # Materialize DigestEntry for each included cluster
    entries: list[DigestEntry] = []
    for rank, (assessment, cluster, rep_story, rep_facts) in enumerate(included, start=1):
        # Resolve representative source name for the "read more" link
        source_name: Optional[str] = None
        if rep_story and rep_story.source_id:
            source = db.get(Source, rep_story.source_id)
            source_name = source.name if source else None

        entry = DigestEntry(
            digest_run_id=run.id,
            event_cluster_id=cluster.id,
            rank=rank,
            final_score=assessment.final_score,
            title=rep_story.title if rep_story else None,
            canonical_summary_en=rep_facts.canonical_summary_en if rep_facts else None,
            canonical_summary_ru=rep_facts.canonical_summary_ru if rep_facts else None,
            why_it_matters_en=assessment.why_it_matters_en,
            why_it_matters_ru=assessment.why_it_matters_ru,
            source_url=rep_story.canonical_url or rep_story.url if rep_story else None,
            source_name=source_name,
        )
        db.add(entry)
        entries.append(entry)

    db.commit()
    db.refresh(run)
    for entry in entries:
        db.refresh(entry)

    logger.info(
        "assemble_digest date=%s section=%s candidates=%d included=%d status=%s rebuilt=%s",
        digest_date,
        section_name,
        total_candidates,
        len(included),
        status,
        was_existing,
    )
    return run, entries, not was_existing
