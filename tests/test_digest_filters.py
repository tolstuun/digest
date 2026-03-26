"""
Tests for Phase 5A: digest relevance filters (companies_business section).

Pure logic tests — no DB, no LLM, no network.
"""
import pytest

from app.digest.filters import (
    BUSINESS_EVENT_TYPES,
    is_business_eligible,
    is_generic_noise,
    is_security_relevant,
    should_include_in_companies_business,
)


# ── is_business_eligible ──────────────────────────────────────────────────────

def test_funding_is_eligible():
    assert is_business_eligible("funding") is True


def test_mna_is_eligible():
    assert is_business_eligible("mna") is True


def test_earnings_is_eligible():
    assert is_business_eligible("earnings") is True


def test_executive_change_is_eligible():
    assert is_business_eligible("executive_change") is True


def test_partnership_is_eligible():
    assert is_business_eligible("partnership") is True


def test_product_launch_is_eligible():
    assert is_business_eligible("product_launch") is True


def test_breach_not_eligible():
    assert is_business_eligible("breach") is False


def test_conference_not_eligible():
    assert is_business_eligible("conference") is False


def test_regulation_not_eligible():
    assert is_business_eligible("regulation") is False


def test_other_not_eligible():
    assert is_business_eligible("other") is False


def test_unknown_not_eligible():
    assert is_business_eligible("unknown") is False


def test_none_not_eligible():
    assert is_business_eligible(None) is False


def test_all_business_types_covered():
    expected = {"funding", "mna", "earnings", "executive_change", "partnership", "product_launch"}
    assert BUSINESS_EVENT_TYPES == expected


# ── is_security_relevant ──────────────────────────────────────────────────────

def test_security_source_passes():
    assert is_security_relevant(
        title="Company raises funds",
        summary_en="Generic summary",
        company_names=["Corp"],
        source_name="Dark Reading",
    ) is True


def test_security_keyword_in_title_passes():
    assert is_security_relevant(
        title="CrowdStrike-backed cybersecurity firm raises $50M",
        summary_en="A new round of funding.",
        company_names=["Acme"],
        source_name="TechCrunch",
    ) is True


def test_security_keyword_in_summary_passes():
    assert is_security_relevant(
        title="Generic tech funding",
        summary_en="The company focuses on ransomware detection and response.",
        company_names=["Acme"],
        source_name="TechCrunch",
    ) is True


def test_known_vendor_in_company_names_passes():
    assert is_security_relevant(
        title="Acquisition announced",
        summary_en="Deal terms not disclosed.",
        company_names=["CrowdStrike", "Acme Corp"],
        source_name="Bloomberg",
    ) is True


def test_generic_title_no_signals_fails():
    assert is_security_relevant(
        title="Company raises $100M in Series D",
        summary_en="The round was led by Sequoia.",
        company_names=["Coffee Corp"],
        source_name="TechCrunch",
    ) is False


def test_none_source_name_ok():
    # Security keyword in summary should still pass
    assert is_security_relevant(
        title="Funding round",
        summary_en="Focused on endpoint security solutions.",
        company_names=["Acme"],
        source_name=None,
    ) is True


# ── is_generic_noise ──────────────────────────────────────────────────────────

def test_tiktok_is_noise():
    assert is_generic_noise(title="TikTok raises funding", summary_en="Social media giant.") is True


def test_spotify_is_noise():
    assert is_generic_noise(
        title="Spotify acquires podcast studio",
        summary_en="Music streaming deal.",
    ) is True


def test_security_related_not_noise():
    assert is_generic_noise(
        title="CrowdStrike raises $200M",
        summary_en="The cybersecurity firm will use funds for expansion.",
    ) is False


def test_empty_strings_not_noise():
    assert is_generic_noise(title="", summary_en="") is False


def test_none_inputs_not_noise():
    assert is_generic_noise(title=None, summary_en=None) is False


# ── should_include_in_companies_business ─────────────────────────────────────

def test_full_check_passes_for_security_funding():
    assert should_include_in_companies_business(
        event_type="funding",
        title="Wiz raises $1B in funding round",
        summary_en="Cloud security firm Wiz secures $1B Series E.",
        company_names=["Wiz"],
        source_name="TechCrunch",
    ) is True


def test_full_check_fails_for_wrong_event_type():
    assert should_include_in_companies_business(
        event_type="breach",
        title="CrowdStrike breach affects millions",
        summary_en="Major cybersecurity incident.",
        company_names=["CrowdStrike"],
        source_name="Dark Reading",
    ) is False


def test_full_check_fails_when_no_security_signal():
    assert should_include_in_companies_business(
        event_type="funding",
        title="Coffee chain raises $50M",
        summary_en="The coffee company will open 100 new locations.",
        company_names=["CoffeeCo"],
        source_name="Forbes",
    ) is False


def test_full_check_passes_via_source_name():
    assert should_include_in_companies_business(
        event_type="mna",
        title="Acquisition announced",
        summary_en="Deal terms were not disclosed.",
        company_names=["TechCorp"],
        source_name="SecurityWeek",
    ) is True


def test_full_check_fails_for_unknown_event_type():
    assert should_include_in_companies_business(
        event_type="unknown",
        title="CrowdStrike news",
        summary_en="cybersecurity company update",
        company_names=["CrowdStrike"],
        source_name="Dark Reading",
    ) is False
