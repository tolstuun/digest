"""
Tests for Phase 4E: daily pipeline orchestration.

All LLM boundaries (extract_facts_llm, assess_cluster_llm) and Telegram HTTP
are mocked. No real network calls.
"""
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config import AppConfig, DatabaseConfig, LLMConfig, SchedulerConfig, Settings, TelegramConfig
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_step import PipelineRunStep
from app.models.raw_item import RawItem
from app.models.source import Source
from app.orchestration.service import run_daily_pipeline


# ── helpers ───────────────────────────────────────────────────────────────────

TARGET_DATE = date(2026, 3, 25)


def _make_settings(
    telegram_enabled: bool = False,
    publish_telegram_by_default: bool = False,
) -> Settings:
    return Settings(
        config_path="test",
        app=AppConfig(public_base_url="https://example.com"),
        database=DatabaseConfig(),
        llm=LLMConfig(api_key="test-key"),
        telegram=TelegramConfig(
            enabled=telegram_enabled,
            bot_token="tok",
            chat_id="-100",
        ),
        scheduler=SchedulerConfig(
            enabled=False,
            publish_telegram_by_default=publish_telegram_by_default,
        ),
    )


def _make_source(db, name: str = "Test Source") -> Source:
    src = Source(
        name=name,
        type="rss",
        url="https://example.com/feed.rss",
        enabled=True,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


# ── pipeline_run model ────────────────────────────────────────────────────────


def test_pipeline_run_created(db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    assert run.id is not None
    assert run.run_date == TARGET_DATE
    assert run.trigger_type == "manual"
    assert run.status == "running"


def test_pipeline_run_step_created(db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    step = PipelineRunStep(
        pipeline_run_id=run.id,
        step_name="ingest",
        status="success",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        details_json={"new": 3},
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    assert step.id is not None
    assert step.pipeline_run_id == run.id
    assert step.step_name == "ingest"
    assert step.details_json == {"new": 3}


def test_pipeline_run_step_cascade_deletes_with_run(db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    step = PipelineRunStep(
        pipeline_run_id=run.id,
        step_name="ingest",
        status="success",
    )
    db.add(step)
    db.commit()
    run_id = run.id

    db.delete(run)
    db.commit()

    remaining = db.query(PipelineRunStep).filter_by(pipeline_run_id=run_id).count()
    assert remaining == 0


# ── orchestration service ─────────────────────────────────────────────────────


def _mock_ingest_returns_empty(db_arg, source_arg):
    return {"fetched": 0, "new": 0, "skipped": 0, "error": None}


def _run_full_pipeline_mocked(db, run_date=None, publish_telegram=False):
    """Helper: run pipeline with all LLM/network boundaries mocked."""
    cfg = _make_settings()
    run_date = run_date or TARGET_DATE
    with (
        patch("app.orchestration.service.ingest_source", side_effect=_mock_ingest_returns_empty),
        patch("app.extraction.service.extract_facts_llm") as mock_llm,
        patch("app.scoring.llm.assess_cluster_llm") as mock_assess_llm,
        patch("app.publishing.service.send_telegram_message", return_value="42"),
    ):
        # Mock LLM to return valid extraction result
        mock_llm.return_value = MagicMock(
            event_type="funding",
            company_names=["Acme Corp"],
            person_names=[],
            product_names=[],
            geography_names=[],
            amount_text="50M",
            currency="USD",
            source_language="en",
            canonical_summary_en="Acme raised $50M.",
            canonical_summary_ru="Acme привлекла $50M.",
            extraction_confidence=0.9,
        )
        mock_assess_llm.return_value = MagicMock(
            primary_section="companies_business",
            llm_score=0.8,
            include_in_digest=True,
            why_it_matters_en="Big deal.",
            why_it_matters_ru="Важная сделка.",
            editorial_notes=None,
        )
        return run_daily_pipeline(
            db=db,
            run_date=run_date,
            trigger_type="manual",
            publish_telegram=publish_telegram,
            cfg=cfg,
        )


def test_orchestration_creates_pipeline_run(db):
    summary = _run_full_pipeline_mocked(db)

    assert "pipeline_run_id" in summary
    run = db.get(PipelineRun, uuid.UUID(summary["pipeline_run_id"]))
    assert run is not None
    assert run.run_date == TARGET_DATE
    assert run.trigger_type == "manual"


def test_orchestration_run_status_success_on_no_data(db):
    """With no sources, pipeline should succeed (all steps pass with empty data)."""
    summary = _run_full_pipeline_mocked(db)
    assert summary["status"] == "success"
    assert summary["failed_step"] is None


def test_orchestration_creates_steps_for_all_stages(db):
    summary = _run_full_pipeline_mocked(db)
    run_id = uuid.UUID(summary["pipeline_run_id"])
    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run_id).all()
    step_names = [s.step_name for s in steps]

    assert "ingest" in step_names
    assert "normalize" in step_names
    assert "extract_facts" in step_names
    assert "cluster_event" in step_names
    assert "assess" in step_names
    assert "assemble_digest" in step_names
    assert "render_digest" in step_names
    assert "publish_telegram" in step_names


def test_orchestration_all_steps_success_or_skipped(db):
    summary = _run_full_pipeline_mocked(db)
    run_id = uuid.UUID(summary["pipeline_run_id"])
    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run_id).all()
    for step in steps:
        assert step.status in ("success", "skipped"), (
            f"Step {step.step_name} has unexpected status {step.status}"
        )


def test_orchestration_steps_have_started_and_finished_at(db):
    summary = _run_full_pipeline_mocked(db)
    run_id = uuid.UUID(summary["pipeline_run_id"])
    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run_id).all()
    for step in steps:
        assert step.started_at is not None
        assert step.finished_at is not None


def test_orchestration_run_has_started_and_finished_at(db):
    summary = _run_full_pipeline_mocked(db)
    run = db.get(PipelineRun, uuid.UUID(summary["pipeline_run_id"]))
    assert run.started_at is not None
    assert run.finished_at is not None


def test_orchestration_rerun_creates_new_pipeline_run(db):
    s1 = _run_full_pipeline_mocked(db)
    s2 = _run_full_pipeline_mocked(db)

    assert s1["pipeline_run_id"] != s2["pipeline_run_id"]
    count = db.query(PipelineRun).filter_by(run_date=TARGET_DATE).count()
    assert count == 2


def test_orchestration_failure_marks_step_and_run_failed(db):
    # The normalize step queries the DB directly, so we patch it to raise
    cfg = _make_settings()
    with patch(
        "app.orchestration.service._run_normalize",
        side_effect=RuntimeError("DB error in normalize"),
    ):
        summary = run_daily_pipeline(
            db=db,
            run_date=TARGET_DATE,
            trigger_type="manual",
            publish_telegram=False,
            cfg=cfg,
        )

    assert summary["status"] == "failed"
    assert summary["failed_step"] == "normalize"

    run = db.get(PipelineRun, uuid.UUID(summary["pipeline_run_id"]))
    assert run.status == "failed"
    assert run.error_message is not None

    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run.id).all()
    failed_steps = [s for s in steps if s.status == "failed"]
    assert len(failed_steps) == 1
    assert failed_steps[0].step_name == "normalize"


def test_orchestration_failure_stops_remaining_steps(db):
    cfg = _make_settings()
    with patch(
        "app.orchestration.service._run_normalize",
        side_effect=RuntimeError("DB error in normalize"),
    ):
        summary = run_daily_pipeline(
            db=db,
            run_date=TARGET_DATE,
            trigger_type="manual",
            publish_telegram=False,
            cfg=cfg,
        )

    run = db.get(PipelineRun, uuid.UUID(summary["pipeline_run_id"]))
    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run.id).all()
    step_names = [s.step_name for s in steps]
    # ingest succeeds, normalize fails — normalize should be the last step recorded
    assert "ingest" in step_names
    assert "normalize" in step_names
    assert "extract_facts" not in step_names  # stopped before this


def test_orchestration_publish_skipped_when_telegram_disabled(db):
    summary = _run_full_pipeline_mocked(db, publish_telegram=False)

    run_id = uuid.UUID(summary["pipeline_run_id"])
    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run_id).all()
    publish_step = next(s for s in steps if s.step_name == "publish_telegram")
    assert publish_step.status == "success"
    assert publish_step.details_json.get("skipped") is True


def test_orchestration_publish_skipped_when_flag_false(db):
    cfg = _make_settings(telegram_enabled=False)
    with (
        patch("app.orchestration.service.ingest_source", side_effect=_mock_ingest_returns_empty),
        patch("app.extraction.service.extract_facts_llm"),
        patch("app.scoring.llm.assess_cluster_llm"),
    ):
        summary = run_daily_pipeline(
            db=db,
            run_date=TARGET_DATE,
            trigger_type="manual",
            publish_telegram=False,
            cfg=cfg,
        )

    run_id = uuid.UUID(summary["pipeline_run_id"])
    steps = db.query(PipelineRunStep).filter_by(pipeline_run_id=run_id).all()
    publish_step = next(s for s in steps if s.step_name == "publish_telegram")
    assert publish_step.details_json.get("skipped") is True


def test_orchestration_with_source_ingests_and_normalizes(db):
    """End-to-end flow: source present, ingest creates raw item, normalize creates story."""
    _make_source(db)

    def _mock_ingest(db_arg, source_arg):
        # Actually persist a raw item so normalize step has work to do
        raw = RawItem(
            source_id=source_arg.id,
            external_id="test-guid",
            content_hash="abc123",
            title="Acme raises $50M",
            url="https://example.com/acme",
        )
        db_arg.add(raw)
        db_arg.commit()
        return {"fetched": 1, "new": 1, "skipped": 0, "error": None}

    from app.extraction.schemas import ExtractionResult

    cfg = _make_settings()
    with (
        patch("app.orchestration.service.ingest_source", side_effect=_mock_ingest),
        patch("app.extraction.service.extract_facts_llm") as mock_llm,
        patch("app.scoring.llm.assess_cluster_llm") as mock_assess,
    ):
        mock_llm.return_value = ExtractionResult(
            event_type="funding",
            company_names=["Acme Corp"],
            person_names=[],
            product_names=[],
            geography_names=[],
            amount_text="50M",
            currency="USD",
            source_language="en",
            canonical_summary_en="Acme raised.",
            canonical_summary_ru="Acme привлекла.",
            extraction_confidence=0.9,
        )
        mock_assess.return_value = MagicMock(
            primary_section="companies_business",
            llm_score=0.8, include_in_digest=True,
            why_it_matters_en="Big.", why_it_matters_ru="Важно.",
            editorial_notes=None,
        )
        summary = run_daily_pipeline(
            db=db, run_date=TARGET_DATE, trigger_type="manual",
            publish_telegram=False, cfg=cfg,
        )

    assert summary["status"] == "success"
    # normalize step should report at least 1 new story
    norm = summary["step_results"].get("normalize", {})
    assert norm.get("new", 0) >= 1


# ── GET /pipeline-runs/ ───────────────────────────────────────────────────────


def test_list_pipeline_runs_empty(client):
    resp = client.get("/pipeline-runs/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_pipeline_runs_returns_runs(client, db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()

    resp = client.get("/pipeline-runs/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["run_date"] == str(TARGET_DATE)
    assert data[0]["status"] == "success"


# ── GET /pipeline-runs/{id} ───────────────────────────────────────────────────


def test_get_pipeline_run_detail(client, db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    step = PipelineRunStep(
        pipeline_run_id=run.id,
        step_name="ingest",
        status="success",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        details_json={"new": 2},
    )
    db.add(step)
    db.commit()

    resp = client.get(f"/pipeline-runs/{run.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(run.id)
    assert data["status"] == "success"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["step_name"] == "ingest"
    assert data["steps"][0]["details_json"] == {"new": 2}


def test_get_pipeline_run_not_found(client):
    resp = client.get(f"/pipeline-runs/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── POST /admin/pipeline-runs/run-daily ───────────────────────────────────────


def test_admin_run_daily_endpoint(client, db):
    with (
        patch("app.routers.admin.run_daily_pipeline") as mock_run,
    ):
        mock_run.return_value = {
            "pipeline_run_id": str(uuid.uuid4()),
            "run_date": str(TARGET_DATE),
            "status": "success",
            "trigger_type": "manual",
            "failed_step": None,
            "step_results": {},
        }
        resp = client.post(
            "/admin/pipeline-runs/run-daily",
            json={"run_date": str(TARGET_DATE)},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    mock_run.assert_called_once()


def test_admin_run_daily_publish_flag_passed(client, db):
    with patch("app.routers.admin.run_daily_pipeline") as mock_run:
        mock_run.return_value = {
            "pipeline_run_id": str(uuid.uuid4()),
            "run_date": str(TARGET_DATE),
            "status": "success",
            "trigger_type": "manual",
            "failed_step": None,
            "step_results": {},
        }
        client.post(
            "/admin/pipeline-runs/run-daily",
            json={"run_date": str(TARGET_DATE), "publish_telegram": True},
        )

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("publish_telegram") is True


# ── scheduler config ──────────────────────────────────────────────────────────


def test_scheduler_config_defaults():
    from app.config import load_settings
    s = load_settings(config_path="/nonexistent/path")
    assert s.scheduler.enabled is False
    assert s.scheduler.daily_time_utc == "06:00"
    assert s.scheduler.publish_telegram_by_default is False


def test_scheduler_config_loaded_from_yaml(tmp_path):
    from app.config import load_settings
    f = tmp_path / "settings.yaml"
    f.write_text(
        "scheduler:\n"
        "  enabled: true\n"
        "  daily_time_utc: '08:30'\n"
        "  publish_telegram_by_default: true\n"
    )
    s = load_settings(config_path=str(f))
    assert s.scheduler.enabled is True
    assert s.scheduler.daily_time_utc == "08:30"
    assert s.scheduler.publish_telegram_by_default is True


def test_scheduler_start_disabled_does_nothing():
    from app.config import Settings, SchedulerConfig
    from app.scheduler import start_scheduler, stop_scheduler, _scheduler
    cfg = Settings(scheduler=SchedulerConfig(enabled=False))
    start_scheduler(cfg)
    # Should not have started anything
    from app.scheduler import _scheduler as sc
    assert sc is None


def test_scheduler_start_enabled_creates_job():
    from app.config import Settings, SchedulerConfig
    from app.scheduler import start_scheduler, stop_scheduler
    cfg = Settings(scheduler=SchedulerConfig(enabled=True, daily_time_utc="06:00"))
    try:
        start_scheduler(cfg)
        from app.scheduler import _scheduler as sc
        assert sc is not None
        assert sc.running
        jobs = sc.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "daily_pipeline"
    finally:
        stop_scheduler()


# ── UI pipeline-runs page ─────────────────────────────────────────────────────


def test_ui_pipeline_runs_page_loads_empty(client):
    resp = client.get("/ui/pipeline-runs")
    assert resp.status_code == 200
    assert "Pipeline Runs" in resp.text


def test_ui_pipeline_runs_page_shows_run(client, db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()

    resp = client.get("/ui/pipeline-runs")
    assert resp.status_code == 200
    assert str(TARGET_DATE) in resp.text
    assert "success" in resp.text


def test_ui_run_daily_action_redirects(client):
    with patch("app.routers.ui.run_daily_pipeline") as mock_run:
        mock_run.return_value = {
            "pipeline_run_id": str(uuid.uuid4()),
            "run_date": str(TARGET_DATE),
            "status": "success",
            "trigger_type": "manual",
            "failed_step": None,
            "step_results": {},
        }
        resp = client.post(
            "/ui/pipeline-runs/run-daily",
            data={"run_date": str(TARGET_DATE)},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "/ui/pipeline-runs" in resp.headers["location"]


def test_ui_run_daily_failure_flash(client):
    with patch("app.routers.ui.run_daily_pipeline") as mock_run:
        mock_run.return_value = {
            "pipeline_run_id": str(uuid.uuid4()),
            "run_date": str(TARGET_DATE),
            "status": "failed",
            "trigger_type": "manual",
            "failed_step": "ingest",
            "step_results": {},
        }
        resp = client.post(
            "/ui/pipeline-runs/run-daily",
            data={"run_date": str(TARGET_DATE)},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "err" in resp.headers["location"]


def test_ui_run_daily_invalid_date(client):
    resp = client.post(
        "/ui/pipeline-runs/run-daily",
        data={"run_date": "not-a-date"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "err" in resp.headers["location"]
