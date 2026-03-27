"""
Digest-writing service.

write_digest_entries() iterates over all DigestEntry rows for a DigestRun,
calls the LLM to produce final polished copy in the configured language,
and updates each entry with final_summary and final_why_it_matters.

Idempotent: entries that already have final_summary set are skipped unless force=True.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.digest.filters import should_include_in_companies_business
from app.digest.service import SECTION_NAME
from app.digest_writer.llm import write_digest_entry_llm
from app.digest_writer.schemas import DigestEntryInput
from app.llm_usage.service import record_usage
from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.story_facts import StoryFacts

logger = logging.getLogger(__name__)

_STAGE = "write_digest"


def write_digest_entries(
    db: Session,
    run: DigestRun,
    cfg: Settings,
    force: bool = False,
) -> dict:
    """
    Run the digest-writing stage for all entries in a DigestRun.

    For each entry:
      - Loads cluster facts (event_type, company_names, amount_text, currency)
      - Calls write_digest_entry_llm to produce final_summary + final_why_it_matters
      - Updates the entry and records LLM usage

    force=True re-writes entries that already have final_summary set.

    Returns {"total": N, "written": N, "skipped": N, "errors": N}.
    """
    entries = (
        db.query(DigestEntry)
        .filter_by(digest_run_id=run.id)
        .order_by(DigestEntry.rank)
        .all()
    )

    written = skipped = errors = 0
    output_language = cfg.digest.output_language
    model_name = cfg.digest.model_writing
    api_key = cfg.llm.api_key

    for entry in entries:
        if entry.final_summary and not force:
            skipped += 1
            continue

        # Load cluster facts for richer LLM input
        event_type = "unknown"
        company_names: list[str] = []
        amount_text: Optional[str] = None
        currency: Optional[str] = None

        if entry.event_cluster_id:
            cluster = db.get(EventCluster, entry.event_cluster_id)
            if cluster:
                event_type = cluster.event_type or "unknown"
                if cluster.representative_story_id:
                    facts = (
                        db.query(StoryFacts)
                        .filter_by(story_id=cluster.representative_story_id)
                        .first()
                    )
                    if facts:
                        company_names = facts.company_names or []
                        amount_text = facts.amount_text
                        currency = facts.currency

        # Relevance gate: skip entries that fail the companies_business filter.
        # Only checked when a cluster is attached; entries without a cluster pass.
        if entry.event_cluster_id and run.section_name == SECTION_NAME:
            if not should_include_in_companies_business(
                event_type=event_type,
                title=entry.title,
                summary_en=entry.canonical_summary_en,
                company_names=company_names,
                source_name=entry.source_name,
            ):
                skipped += 1
                logger.debug(
                    "write_digest entry=%s skipped: fails companies_business relevance gate",
                    entry.id,
                )
                continue

        # Also check assessment for why_it_matters
        why_it_matters_en: Optional[str] = entry.why_it_matters_en

        entry_input = DigestEntryInput(
            entry_id=str(entry.id),
            title=entry.title or "",
            event_type=event_type,
            company_names=company_names,
            amount_text=amount_text,
            currency=currency,
            canonical_summary_en=entry.canonical_summary_en,
            why_it_matters_en=why_it_matters_en,
            output_language=output_language,
        )

        try:
            result, usage = write_digest_entry_llm(entry_input, model_name, api_key)
            entry.final_summary = result.final_summary
            entry.final_why_it_matters = result.final_why_it_matters
            db.commit()
            record_usage(db, _STAGE, usage)
            written += 1
            logger.info(
                "write_digest entry=%s lang=%s written",
                entry.id, output_language,
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("write_digest entry=%s failed: %s", entry.id, exc)
            db.rollback()

    return {
        "total": len(entries),
        "written": written,
        "skipped": skipped,
        "errors": errors,
    }
