"""
Tests for Phase 3B: prioritization / editorial scoring.

LLM calls are mocked — no real network requests are made.
"""
import hashlib
from unittest.mock import patch

import pytest

from app.clustering.rules import build_cluster_key
from app.llm_usage.schemas import LlmUsageInfo
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.scoring.rules import compute_rule_score
from app.scoring.schemas import ClusterAssessment
from app.scoring.service import assess_cluster


# ── test fixtures ─────────────────────────────────────────────────────────────

def _make_cluster(
    db,
    event_type: str = "funding",
    company_names: list | None = None,
    amount_text: str | None = "$50M",
    currency: str | None = "USD",
    source_priority: int = 0,
    summary_suffix: str = "",
) -> tuple[EventCluster, Story, StoryFacts]:
    """Create a complete Source→RawItem→Story→StoryFacts→EventCluster chain."""
    if company_names is None:
        company_names = ["Acme Corp"]

    source = Source(
        name="Feed",
        type="rss",
        url="https://example.com/feed",
        enabled=True,
        priority=source_priority,
    )
    db.add(source)
    db.flush()

    summary = f"{company_names[0]} {event_type} {amount_text} {summary_suffix}".strip()
    title = f"{company_names[0]} {event_type}"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(summary.encode()).hexdigest(),
        title=title,
        url="https://example.com/article",
        raw_payload={"title": title, "summary": summary},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title=title,
        url="https://example.com/article",
        canonical_url="https://example.com/article",
    )
    db.add(story)
    db.flush()

    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type=event_type,
        company_names=company_names,
        person_names=[],
        product_names=[],
        geography_names=[],
        amount_text=amount_text,
        currency=currency,
        source_language="en",
        canonical_summary_en=f"{company_names[0]} raised {amount_text}.",
        canonical_summary_ru=f"{company_names[0]} привлекла {amount_text}.",
        extraction_confidence=0.92,
    )
    db.add(facts)
    db.flush()

    cluster_key = build_cluster_key(event_type, company_names, amount_text, currency)
    cluster = EventCluster(
        cluster_key=cluster_key or f"{event_type}-{company_names[0]}-fallback",
        event_type=event_type,
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()
    story.event_cluster_id = cluster.id
    db.commit()
    db.refresh(cluster)
    db.refresh(story)
    db.refresh(facts)
    return cluster, story, facts


def _mock_usage() -> LlmUsageInfo:
    return LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50)


def _mock_assessment(**overrides) -> ClusterAssessment:
    defaults = dict(
        primary_section="companies_business",
        llm_score=0.90,
        include_in_digest=True,
        why_it_matters_en="Significant funding round for a cybersecurity company.",
        why_it_matters_ru="Значительный раунд финансирования компании в сфере кибербезопасности.",
        editorial_notes="Strong deal; include in business section.",
    )
    defaults.update(overrides)
    return ClusterAssessment(**defaults)


# ── compute_rule_score (pure, no DB) ─────────────────────────────────────────

def test_rule_score_mna_with_amount_is_high():
    score = compute_rule_score("mna", story_count=1, has_amount=True, has_currency=True)
    assert score >= 0.90


def test_rule_score_funding_with_amount_higher_than_funding_without():
    score_with = compute_rule_score("funding", story_count=1, has_amount=True, has_currency=True)
    score_without = compute_rule_score("funding", story_count=1, has_amount=False, has_currency=False)
    assert score_with > score_without


def test_rule_score_conference_is_low():
    score = compute_rule_score("conference", story_count=1, has_amount=False, has_currency=False)
    assert score < 0.40


def test_rule_score_multi_story_higher_than_single():
    score_single = compute_rule_score("funding", story_count=1, has_amount=False, has_currency=False)
    score_multi = compute_rule_score("funding", story_count=4, has_amount=False, has_currency=False)
    assert score_multi > score_single


def test_rule_score_never_exceeds_1():
    score = compute_rule_score("mna", story_count=20, has_amount=True, has_currency=True, max_source_priority=100)
    assert score <= 1.0


def test_rule_score_strong_business_event_higher_than_weak():
    strong = compute_rule_score("funding", story_count=1, has_amount=True, has_currency=True)
    weak = compute_rule_score("conference", story_count=1, has_amount=False, has_currency=False)
    assert strong > weak


def test_rule_score_source_priority_bonus():
    score_low = compute_rule_score("funding", story_count=1, has_amount=False, has_currency=False, max_source_priority=0)
    score_high = compute_rule_score("funding", story_count=1, has_amount=False, has_currency=False, max_source_priority=10)
    assert score_high >= score_low


def test_rule_score_returns_float():
    score = compute_rule_score("funding", story_count=1, has_amount=True, has_currency=True)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


# ── ClusterAssessment schema validation (no DB) ───────────────────────────────

def test_cluster_assessment_valid():
    a = _mock_assessment()
    assert a.primary_section == "companies_business"
    assert 0.0 <= a.llm_score <= 1.0
    assert isinstance(a.include_in_digest, bool)


def test_cluster_assessment_all_valid_sections():
    for section in ["companies_business", "incidents", "conferences", "regulation", "other"]:
        a = _mock_assessment(primary_section=section)
        assert a.primary_section == section


def test_cluster_assessment_invalid_section_rejected():
    with pytest.raises(Exception):
        _mock_assessment(primary_section="random_section")


def test_cluster_assessment_llm_score_too_high_rejected():
    with pytest.raises(Exception):
        _mock_assessment(llm_score=1.5)


def test_cluster_assessment_llm_score_negative_rejected():
    with pytest.raises(Exception):
        _mock_assessment(llm_score=-0.1)


# ── assess_cluster service (DB-backed, LLM mocked) ───────────────────────────

def test_assess_creates_assessment(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assessment, created = assess_cluster(db, cluster)
    assert created is True
    assert assessment.event_cluster_id == cluster.id


def test_assess_persists_to_db(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assess_cluster(db, cluster)
    count = db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).count()
    assert count == 1


def test_assess_stores_rule_score(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.rule_score is not None
    assert 0.0 <= assessment.rule_score <= 1.0


def test_assess_stores_llm_score(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(llm_score=0.88), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.llm_score == 0.88


def test_assess_final_score_is_weighted_combination(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(llm_score=0.90), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    expected = round(0.4 * assessment.rule_score + 0.6 * assessment.llm_score, 4)
    assert abs(assessment.final_score - expected) < 0.0001


def test_assess_stores_model_name(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.model_name is not None
    assert len(assessment.model_name) > 0


def test_assess_stores_raw_model_output(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.raw_model_output is not None
    assert isinstance(assessment.raw_model_output, dict)
    assert "primary_section" in assessment.raw_model_output


def test_assess_sets_assessed_at(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.assessed_at is not None


def test_assess_repeated_updates_not_duplicates(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(llm_score=0.70), _mock_usage())):
        a1, created1 = assess_cluster(db, cluster)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(llm_score=0.95), _mock_usage())):
        a2, created2 = assess_cluster(db, cluster)
    assert created1 is True
    assert created2 is False
    assert a1.id == a2.id
    assert a2.llm_score == 0.95
    assert db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).count() == 1


def test_assess_stores_section_and_digest_flag(db):
    cluster, story, facts = _make_cluster(db)
    mock = _mock_assessment(primary_section="incidents", include_in_digest=False)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(mock, _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.primary_section == "incidents"
    assert assessment.include_in_digest is False


def test_assess_stores_why_it_matters(db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assessment, _ = assess_cluster(db, cluster)
    assert assessment.why_it_matters_en
    assert assessment.why_it_matters_ru


# ── GET /event-clusters/{id}/assessment ──────────────────────────────────────

def test_get_assessment_returns_200(client, db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        assess_cluster(db, cluster)
    resp = client.get(f"/event-clusters/{cluster.id}/assessment")
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_cluster_id"] == str(cluster.id)
    assert data["primary_section"] == "companies_business"
    assert data["include_in_digest"] is True
    assert data["final_score"] is not None
    assert data["why_it_matters_en"] != ""
    assert data["why_it_matters_ru"] != ""


def test_get_assessment_no_assessment_yet_returns_404(client, db):
    cluster, story, facts = _make_cluster(db)
    resp = client.get(f"/event-clusters/{cluster.id}/assessment")
    assert resp.status_code == 404


def test_get_assessment_cluster_not_found_returns_404(client):
    resp = client.get("/event-clusters/00000000-0000-0000-0000-000000000000/assessment")
    assert resp.status_code == 404


# ── POST /admin/event-clusters/{id}/assess ────────────────────────────────────

def test_assess_endpoint_success(client, db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.routers.admin.assess_cluster") as mock_fn:
        mock_a = EventClusterAssessment(
            event_cluster_id=cluster.id,
            primary_section="companies_business",
            rule_score=0.85,
            llm_score=0.90,
            final_score=0.88,
            include_in_digest=True,
            why_it_matters_en="Test.",
            why_it_matters_ru="Тест.",
            editorial_notes="",
            model_name="claude-haiku-4-5-20251001",
        )
        mock_fn.return_value = (mock_a, True)
        resp = client.post(f"/admin/event-clusters/{cluster.id}/assess")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cluster_id"] == str(cluster.id)
    assert data["created"] is True
    assert data["include_in_digest"] is True
    assert data["final_score"] == 0.88


def test_assess_endpoint_cluster_not_found(client):
    resp = client.post("/admin/event-clusters/00000000-0000-0000-0000-000000000000/assess")
    assert resp.status_code == 404


def test_assess_endpoint_idempotent(client, db):
    cluster, story, facts = _make_cluster(db)
    with patch("app.scoring.service.assess_cluster_llm", return_value=(_mock_assessment(), _mock_usage())):
        resp1 = client.post(f"/admin/event-clusters/{cluster.id}/assess")
        resp2 = client.post(f"/admin/event-clusters/{cluster.id}/assess")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["cluster_id"] == resp2.json()["cluster_id"]
    assert resp2.json()["created"] is False


# ── early relevance gate in _run_assess ──────────────────────────────────────

def test_run_assess_skips_irrelevant_cluster_before_llm(db):
    """Clusters that fail the companies_business gate are skipped before the LLM is called."""
    from app.orchestration.service import _run_assess

    cluster, _, _ = _make_cluster(db, event_type="funding", company_names=["Starbucks Coffee"])
    with patch("app.scoring.service.assess_cluster_llm") as mock_llm:
        result = _run_assess(db)

    mock_llm.assert_not_called()
    assert result["skipped"] == 1
    assert result["assessed"] == 0


def test_run_assess_processes_relevant_cluster(db):
    """Clusters that pass the companies_business gate proceed to LLM assessment."""
    from app.orchestration.service import _run_assess

    cluster, _, _ = _make_cluster(db, event_type="funding", company_names=["CrowdStrike"])
    with patch(
        "app.scoring.service.assess_cluster_llm",
        return_value=(_mock_assessment(), _mock_usage()),
    ):
        result = _run_assess(db)

    assert result["assessed"] == 1
    assert result["skipped"] == 0
