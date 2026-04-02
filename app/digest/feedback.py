"""
Editorial feedback helpers.

Feedback uses latest-wins semantics: the most recent ClusterFeedback row
for a given event_cluster_id is the active override.

Action semantics (enforced in assemble_digest):
  include          → always include, bypass all filters
  exclude          → always hide, suppress regardless of score
  noise            → always hide, mark as irrelevant noise
  section_override → include ONLY in the specified section; hidden elsewhere
"""
import uuid

from sqlalchemy.orm import Session

from app.models.cluster_feedback import ClusterFeedback

VALID_ACTIONS = frozenset({"include", "exclude", "noise", "section_override"})

# Actions that always suppress
_SUPPRESS_ACTIONS = frozenset({"exclude", "noise"})


def record_feedback(
    db: Session,
    event_cluster_id: uuid.UUID,
    action: str,
    section: str | None = None,
    reason: str | None = None,
) -> ClusterFeedback:
    """Append a new feedback row. Latest row wins per cluster."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid feedback action: {action!r}. Must be one of {VALID_ACTIONS}")
    fb = ClusterFeedback(
        event_cluster_id=event_cluster_id,
        action=action,
        section=section,
        reason=reason,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


def get_latest_feedback(
    db: Session,
    event_cluster_id: uuid.UUID,
) -> ClusterFeedback | None:
    """Return the most recent feedback row for a cluster, or None."""
    return (
        db.query(ClusterFeedback)
        .filter_by(event_cluster_id=event_cluster_id)
        .order_by(ClusterFeedback.created_at.desc(), ClusterFeedback.id.desc())
        .first()
    )


def get_all_feedback(db: Session) -> dict[uuid.UUID, ClusterFeedback]:
    """
    Return a mapping of event_cluster_id -> latest ClusterFeedback for all clusters
    that have at least one feedback row.

    Used by assemble_digest to avoid N+1 queries.
    """
    all_rows = (
        db.query(ClusterFeedback)
        .order_by(ClusterFeedback.event_cluster_id, ClusterFeedback.created_at.desc(), ClusterFeedback.id.desc())
        .all()
    )
    # First row per cluster_id wins (already sorted desc)
    result: dict[uuid.UUID, ClusterFeedback] = {}
    for row in all_rows:
        if row.event_cluster_id not in result:
            result[row.event_cluster_id] = row
    return result


def is_suppressed(feedback: ClusterFeedback | None) -> bool:
    """Return True if the feedback action suppresses inclusion."""
    return feedback is not None and feedback.action in _SUPPRESS_ACTIONS


def is_forced_include(feedback: ClusterFeedback | None) -> bool:
    """Return True if the feedback action forces inclusion regardless of filters."""
    return feedback is not None and feedback.action == "include"


def is_section_override(feedback: ClusterFeedback | None, section_name: str) -> bool:
    """
    Return True if the feedback redirects this cluster to section_name.
    Only matches when action==section_override AND section==section_name.
    """
    return (
        feedback is not None
        and feedback.action == "section_override"
        and feedback.section == section_name
    )
