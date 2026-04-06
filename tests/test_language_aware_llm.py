"""
Tests: language-aware LLM pipeline (extraction + scoring).

Covers:
  - extract_facts_llm tool schema requires only EN summary in EN mode
  - extract_facts_llm tool schema requires only RU summary in RU mode
  - extract_facts_llm prompt includes an explicit language instruction
  - assess_cluster_llm tool schema requires only EN why-it-matters in EN mode
  - assess_cluster_llm tool schema requires only RU why-it-matters in RU mode
  - assess_cluster_llm prompt includes an explicit language instruction
  - ExtractionResult accepts missing inactive-language field (defaults to "")
  - ClusterAssessment accepts missing inactive-language field (defaults to "")
  - extract_story_facts passes output_language to the LLM boundary
  - assess_cluster passes output_language to the LLM boundary
  - Existing DB rows with both language fields still work (backward compat)
"""
import hashlib
import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from app.extraction.llm import _build_tool_schema as extraction_build_schema
from app.extraction.llm import extract_facts_llm
from app.extraction.schemas import ExtractionResult
from app.extraction.service import extract_story_facts
from app.llm_usage.schemas import LlmUsageInfo
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.scoring.llm import _build_tool_schema as scoring_build_schema
from app.scoring.llm import assess_cluster_llm
from app.scoring.schemas import ClusterAssessment, ClusterInput
from app.scoring.service import assess_cluster


# ── helpers ───────────────────────────────────────────────────────────────────


def _mock_usage() -> LlmUsageInfo:
    return LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=80, output_tokens=30)


def _make_story(db, suffix: str = "") -> Story:
    n = uuid.uuid4().hex[:8]
    source = Source(name=f"Feed-{n}", type="rss", url=f"https://example.com/{n}", enabled=True)
    db.add(source)
    db.flush()
    content = f"Acme Corp raises $50M {suffix}"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title="Acme Corp Raises $50M",
        url=f"https://example.com/{n}/article",
        raw_payload={"title": "Acme Corp Raises $50M", "summary": content},
    )
    db.add(ri)
    db.flush()
    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title="Acme Corp Raises $50M",
        url=ri.url,
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    return story


def _make_cluster_with_facts(db, suffix: str = "") -> tuple[EventCluster, StoryFacts]:
    story = _make_story(db, suffix=suffix)
    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type="funding",
        company_names=["Acme Corp"],
        source_language="en",
        canonical_summary_en="Acme raised $50M.",
        canonical_summary_ru="Acme привлекла $50M.",
        extraction_confidence=0.9,
    )
    db.add(facts)
    db.flush()
    cluster = EventCluster(
        cluster_key=f"funding-acme-{uuid.uuid4().hex[:6]}",
        event_type="funding",
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    story.event_cluster_id = cluster.id
    db.commit()
    return cluster, facts


# ── extraction tool schema ─────────────────────────────────────────────────────


def test_extraction_schema_en_requires_en_summary():
    schema = extraction_build_schema("en")
    required = schema["input_schema"]["required"]
    assert "canonical_summary_en" in required
    assert "canonical_summary_ru" not in required


def test_extraction_schema_ru_requires_ru_summary():
    schema = extraction_build_schema("ru")
    required = schema["input_schema"]["required"]
    assert "canonical_summary_ru" in required
    assert "canonical_summary_en" not in required


def test_extraction_schema_both_languages_still_in_properties():
    """Both fields remain in properties so the LLM knows they exist."""
    for lang in ("en", "ru"):
        schema = extraction_build_schema(lang)
        props = schema["input_schema"]["properties"]
        assert "canonical_summary_en" in props
        assert "canonical_summary_ru" in props


def test_extraction_schema_unknown_language_defaults_to_en():
    schema = extraction_build_schema("fr")
    required = schema["input_schema"]["required"]
    assert "canonical_summary_en" in required
    assert "canonical_summary_ru" not in required


# ── extraction prompt language instruction ─────────────────────────────────────


def _fake_extraction_create(input_fields: dict):
    """Return a callable that captures messages and returns a mock Anthropic response."""
    def fake_create(**kwargs):
        fake_create.captured_messages = kwargs["messages"]
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="tool_use", input=input_fields)]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        return mock_response
    fake_create.captured_messages = []
    return fake_create


def test_extraction_prompt_en_mode_instructs_skip_ru():
    """In EN mode the prompt explicitly tells the model not to generate RU text."""
    from app.extraction.schemas import StoryInput

    fake = _fake_extraction_create({
        "source_language": "en", "event_type": "funding",
        "company_names": ["Acme"], "person_names": [], "product_names": [],
        "geography_names": [], "extraction_confidence": 0.9,
        "canonical_summary_en": "Acme raised $50M.",
    })

    with patch("app.extraction.llm.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = fake
        extract_facts_llm(StoryInput(story_id="1", title="T", text="B", url=None), output_language="en")

    prompt = fake.captured_messages[0]["content"]
    assert "canonical_summary_ru" in prompt.lower() or "russian" in prompt.lower()
    assert "do not" in prompt.lower() or "omit" in prompt.lower()


def test_extraction_prompt_ru_mode_instructs_skip_en():
    """In RU mode the prompt explicitly tells the model not to generate EN text."""
    from app.extraction.schemas import StoryInput

    fake = _fake_extraction_create({
        "source_language": "ru", "event_type": "funding",
        "company_names": ["Acme"], "person_names": [], "product_names": [],
        "geography_names": [], "extraction_confidence": 0.9,
        "canonical_summary_ru": "Acme привлекла $50M.",
    })

    with patch("app.extraction.llm.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = fake
        extract_facts_llm(StoryInput(story_id="1", title="T", text="B", url=None), output_language="ru")

    prompt = fake.captured_messages[0]["content"]
    assert "canonical_summary_en" in prompt.lower() or "english" in prompt.lower()
    assert "do not" in prompt.lower() or "omit" in prompt.lower()


# ── scoring tool schema ────────────────────────────────────────────────────────


def test_scoring_schema_en_requires_en_why():
    schema = scoring_build_schema("en")
    required = schema["input_schema"]["required"]
    assert "why_it_matters_en" in required
    assert "why_it_matters_ru" not in required


def test_scoring_schema_ru_requires_ru_why():
    schema = scoring_build_schema("ru")
    required = schema["input_schema"]["required"]
    assert "why_it_matters_ru" in required
    assert "why_it_matters_en" not in required


def test_scoring_schema_both_languages_still_in_properties():
    for lang in ("en", "ru"):
        schema = scoring_build_schema(lang)
        props = schema["input_schema"]["properties"]
        assert "why_it_matters_en" in props
        assert "why_it_matters_ru" in props


# ── scoring prompt language instruction ────────────────────────────────────────


def _fake_scoring_create(input_fields: dict):
    def fake_create(**kwargs):
        fake_create.captured_messages = kwargs["messages"]
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="tool_use", input=input_fields)]
        mock_response.usage = MagicMock(input_tokens=80, output_tokens=30)
        return mock_response
    fake_create.captured_messages = []
    return fake_create


def test_scoring_prompt_en_mode_instructs_skip_ru():
    """In EN mode the scoring prompt tells the model not to produce RU text."""
    fake = _fake_scoring_create({
        "primary_section": "companies_business",
        "llm_score": 0.8,
        "include_in_digest": True,
        "why_it_matters_en": "Big deal.",
        "editorial_notes": "",
    })

    with patch("app.scoring.llm.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = fake
        assess_cluster_llm(
            ClusterInput(
                cluster_id="abc", event_type="funding", story_count=1,
                company_names=["Acme"], amount_text=None, currency=None,
                canonical_summary_en="Acme raised $50M.", canonical_summary_ru=None,
                representative_title="Acme raises",
            ),
            output_language="en",
        )

    prompt = fake.captured_messages[0]["content"]
    assert "why_it_matters_ru" in prompt.lower() or "russian" in prompt.lower()
    assert "do not" in prompt.lower() or "omit" in prompt.lower()


def test_scoring_prompt_ru_mode_instructs_skip_en():
    """In RU mode the scoring prompt tells the model not to produce EN text."""
    fake = _fake_scoring_create({
        "primary_section": "companies_business",
        "llm_score": 0.8,
        "include_in_digest": True,
        "why_it_matters_ru": "Крупная сделка.",
        "editorial_notes": "",
    })

    with patch("app.scoring.llm.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = fake
        assess_cluster_llm(
            ClusterInput(
                cluster_id="abc", event_type="funding", story_count=1,
                company_names=["Acme"], amount_text=None, currency=None,
                canonical_summary_en=None, canonical_summary_ru="Acme привлекла $50M.",
                representative_title="Acme raises",
            ),
            output_language="ru",
        )

    prompt = fake.captured_messages[0]["content"]
    assert "why_it_matters_en" in prompt.lower() or "english" in prompt.lower()
    assert "do not" in prompt.lower() or "omit" in prompt.lower()


# ── Pydantic schema tolerates missing inactive field ─────────────────────────


def test_extraction_result_en_mode_ru_field_optional():
    """ExtractionResult must parse successfully when RU summary is absent."""
    result = ExtractionResult(
        source_language="en", event_type="funding",
        company_names=["Acme"], person_names=[], product_names=[],
        geography_names=[], extraction_confidence=0.9,
        canonical_summary_en="Acme raised $50M.",
        # canonical_summary_ru intentionally omitted
    )
    assert result.canonical_summary_en == "Acme raised $50M."
    assert result.canonical_summary_ru == ""


def test_extraction_result_ru_mode_en_field_optional():
    """ExtractionResult must parse successfully when EN summary is absent."""
    result = ExtractionResult(
        source_language="ru", event_type="funding",
        company_names=["Acme"], person_names=[], product_names=[],
        geography_names=[], extraction_confidence=0.9,
        canonical_summary_ru="Acme привлекла $50M.",
        # canonical_summary_en intentionally omitted
    )
    assert result.canonical_summary_ru == "Acme привлекла $50M."
    assert result.canonical_summary_en == ""


def test_cluster_assessment_en_mode_ru_field_optional():
    assessment = ClusterAssessment(
        primary_section="companies_business",
        llm_score=0.8,
        include_in_digest=True,
        why_it_matters_en="Big deal.",
        editorial_notes="",
        # why_it_matters_ru intentionally omitted
    )
    assert assessment.why_it_matters_en == "Big deal."
    assert assessment.why_it_matters_ru == ""


def test_cluster_assessment_ru_mode_en_field_optional():
    assessment = ClusterAssessment(
        primary_section="companies_business",
        llm_score=0.8,
        include_in_digest=True,
        why_it_matters_ru="Крупная сделка.",
        editorial_notes="",
        # why_it_matters_en intentionally omitted
    )
    assert assessment.why_it_matters_ru == "Крупная сделка."
    assert assessment.why_it_matters_en == ""


# ── service layer passes output_language to LLM boundary ─────────────────────


def test_extract_story_facts_passes_output_language_en(db):
    story = _make_story(db, suffix="lang-en")

    mock_result = ExtractionResult(
        source_language="en", event_type="funding",
        company_names=["Acme"], person_names=[], product_names=[],
        geography_names=[], extraction_confidence=0.9,
        canonical_summary_en="Acme raised $50M.",
    )

    with patch("app.extraction.service.extract_facts_llm", return_value=(mock_result, _mock_usage())) as mock_llm:
        extract_story_facts(db, story, output_language="en")

    _, call_kwargs = mock_llm.call_args
    assert call_kwargs.get("output_language") == "en"


def test_extract_story_facts_passes_output_language_ru(db):
    story = _make_story(db, suffix="lang-ru")

    mock_result = ExtractionResult(
        source_language="ru", event_type="funding",
        company_names=["Acme"], person_names=[], product_names=[],
        geography_names=[], extraction_confidence=0.9,
        canonical_summary_ru="Acme привлекла $50M.",
    )

    with patch("app.extraction.service.extract_facts_llm", return_value=(mock_result, _mock_usage())) as mock_llm:
        extract_story_facts(db, story, output_language="ru")

    _, call_kwargs = mock_llm.call_args
    assert call_kwargs.get("output_language") == "ru"


def test_assess_cluster_passes_output_language_en(db):
    cluster, _ = _make_cluster_with_facts(db, suffix="score-en")

    mock_result = ClusterAssessment(
        primary_section="companies_business",
        llm_score=0.8,
        include_in_digest=True,
        why_it_matters_en="Big deal.",
        editorial_notes="",
    )

    with patch("app.scoring.service.assess_cluster_llm", return_value=(mock_result, _mock_usage())) as mock_llm:
        assess_cluster(db, cluster, output_language="en")

    _, call_kwargs = mock_llm.call_args
    assert call_kwargs.get("output_language") == "en"


def test_assess_cluster_passes_output_language_ru(db):
    cluster, _ = _make_cluster_with_facts(db, suffix="score-ru")

    mock_result = ClusterAssessment(
        primary_section="companies_business",
        llm_score=0.8,
        include_in_digest=True,
        why_it_matters_ru="Крупная сделка.",
        editorial_notes="",
    )

    with patch("app.scoring.service.assess_cluster_llm", return_value=(mock_result, _mock_usage())) as mock_llm:
        assess_cluster(db, cluster, output_language="ru")

    _, call_kwargs = mock_llm.call_args
    assert call_kwargs.get("output_language") == "ru"


# ── backward compatibility: existing rows with both languages ─────────────────


def test_existing_bilingual_story_facts_renders_correctly(db):
    """Rows with both canonical_summary_en and _ru fields set continue to work."""
    story = _make_story(db, suffix="bilingual")
    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type="funding",
        company_names=["Acme"],
        source_language="en",
        canonical_summary_en="Acme raised $50M.",
        canonical_summary_ru="Acme привлекла $50M.",
        extraction_confidence=0.9,
    )
    db.add(facts)
    db.commit()
    db.refresh(facts)

    assert facts.canonical_summary_en == "Acme raised $50M."
    assert facts.canonical_summary_ru == "Acme привлекла $50M."


def test_existing_bilingual_assessment_rows_still_accessible(db):
    """EventClusterAssessment rows with both why_it_matters fields continue to work."""
    cluster, _ = _make_cluster_with_facts(db, suffix="bilingual-assess")
    assessment = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section="companies_business",
        include_in_digest=True,
        rule_score=0.6,
        llm_score=0.8,
        final_score=0.72,
        why_it_matters_en="Big deal.",
        why_it_matters_ru="Крупная сделка.",
        editorial_notes="",
        model_name="claude-haiku-4-5-20251001",
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    assert assessment.why_it_matters_en == "Big deal."
    assert assessment.why_it_matters_ru == "Крупная сделка."
