"""
Tests for Phase 5E: LLM token/cost accounting.

No LLM calls. No network calls.
"""
from decimal import Decimal

import pytest

from app.llm_usage.cost import estimate_cost_usd
from app.llm_usage.schemas import LlmUsageInfo
from app.llm_usage.service import record_usage
from app.models.llm_usage import LlmUsage


# ── cost estimation (pure) ────────────────────────────────────────────────────

def test_cost_haiku_known_model():
    cost = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=500)
    assert cost is not None
    assert isinstance(cost, Decimal)
    assert cost > 0


def test_cost_unknown_model_returns_none():
    cost = estimate_cost_usd("gpt-4o", input_tokens=1000, output_tokens=500)
    assert cost is None


def test_cost_zero_tokens():
    cost = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=0, output_tokens=0)
    assert cost == Decimal("0")


def test_cost_scales_with_tokens():
    cost1 = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
    cost2 = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=2000, output_tokens=200)
    assert cost2 == cost1 * 2


def test_cost_output_tokens_more_expensive_than_input():
    # For all models: output price > input price
    cost_input = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=0)
    cost_output = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=0, output_tokens=1000)
    assert cost_output > cost_input


# ── record_usage service (DB-backed) ─────────────────────────────────────────

def test_record_usage_creates_row(db):
    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=200,
        output_tokens=100,
    )
    row = record_usage(db, "extract_facts", usage)

    assert row.id is not None
    assert row.stage_name == "extract_facts"
    assert row.model_name == "claude-haiku-4-5-20251001"
    assert row.input_tokens == 200
    assert row.output_tokens == 100


def test_record_usage_computes_cost(db):
    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=1000,
        output_tokens=500,
    )
    row = record_usage(db, "assess", usage)

    assert row.estimated_cost_usd is not None
    assert row.estimated_cost_usd > 0


def test_record_usage_stores_related_object_id(db):
    import uuid
    obj_id = str(uuid.uuid4())
    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
        related_object_id=obj_id,
    )
    row = record_usage(db, "write_digest", usage)

    assert str(row.related_object_id) == obj_id


def test_record_usage_persists_to_db(db):
    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=50,
        output_tokens=25,
    )
    row = record_usage(db, "extract_facts", usage)

    fetched = db.get(LlmUsage, row.id)
    assert fetched is not None
    assert fetched.stage_name == "extract_facts"


def test_record_multiple_usages(db):
    for stage in ["extract_facts", "assess", "write_digest"]:
        usage = LlmUsageInfo(
            model_name="claude-haiku-4-5-20251001",
            input_tokens=100,
            output_tokens=50,
        )
        record_usage(db, stage, usage)

    rows = db.query(LlmUsage).order_by(LlmUsage.created_at).all()
    stages = [r.stage_name for r in rows]
    assert "extract_facts" in stages
    assert "assess" in stages
    assert "write_digest" in stages


def test_record_usage_null_cost_for_unknown_model(db):
    usage = LlmUsageInfo(
        model_name="unknown-model-xyz",
        input_tokens=100,
        output_tokens=50,
    )
    row = record_usage(db, "extract_facts", usage)
    assert row.estimated_cost_usd is None


# ── GET /llm-usages/ ─────────────────────────────────────────────────────────

def test_list_llm_usages_empty(client):
    resp = client.get("/llm-usages/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_llm_usages_returns_records(client, db):
    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
    )
    record_usage(db, "extract_facts", usage)

    resp = client.get("/llm-usages/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["stage_name"] == "extract_facts"
    assert data[0]["model_name"] == "claude-haiku-4-5-20251001"


def test_list_llm_usages_filter_by_stage(client, db):
    for stage in ["extract_facts", "assess", "assess"]:
        usage = LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=10, output_tokens=5)
        record_usage(db, stage, usage)

    resp = client.get("/llm-usages/?stage_name=assess")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(d["stage_name"] == "assess" for d in data)
