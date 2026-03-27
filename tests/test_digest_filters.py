"""
Tests for digest relevance filters (companies_business section).

Pure logic tests — no DB, no LLM, no network.

Design notes (reflected in tests):
  - companies_business is intentionally strict: only genuine cybersecurity
    business news (funding, M&A, earnings, market moves of security vendors).
  - A security-focused source alone is NOT sufficient to pass; the story
    content must carry an explicit cybersecurity signal.
  - Incidents and regulation are out of scope here; they will get their own
    sections later.
"""
import pytest

from app.digest.filters import (
    BUSINESS_EVENT_TYPES,
    _company_names_have_security_vendor,
    _has_content_security_signal,
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


# ── _has_content_security_signal ──────────────────────────────────────────────

def test_content_signal_keyword_in_title():
    assert _has_content_security_signal(
        title="Startup raises $50M for endpoint security platform",
        summary_en="The round was led by Sequoia.",
        company_names=["Acme"],
    ) is True


def test_content_signal_keyword_in_summary():
    assert _has_content_security_signal(
        title="Generic tech funding",
        summary_en="The company focuses on ransomware detection and response.",
        company_names=["Acme"],
    ) is True


def test_content_signal_vendor_in_company_names():
    assert _has_content_security_signal(
        title="Acquisition announced",
        summary_en="Deal terms not disclosed.",
        company_names=["CrowdStrike", "Acme Corp"],
    ) is True


def test_content_signal_vendor_hint_in_title():
    assert _has_content_security_signal(
        title="Palo Alto Networks raises $500M",
        summary_en="The deal was completed this week.",
        company_names=["PANW"],
    ) is True


def test_content_signal_no_signal_returns_false():
    assert _has_content_security_signal(
        title="Company raises $100M in Series D",
        summary_en="The round was led by Sequoia.",
        company_names=["Coffee Corp"],
    ) is False


def test_content_signal_source_not_considered():
    """Source name must NOT influence the content security signal."""
    # Generic title/summary/company with no security content → False,
    # even if we imagine the caller got this from a security publication.
    assert _has_content_security_signal(
        title="Acquisition announced",
        summary_en="Deal terms were not disclosed.",
        company_names=["TechCorp"],
    ) is False


# ── is_security_relevant (includes source signal) ────────────────────────────

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


def test_whatsapp_is_noise():
    assert is_generic_noise(
        title="WhatsApp launches new privacy feature",
        summary_en="The messaging platform adds end-to-end encryption.",
    ) is True


def test_chatgpt_is_noise():
    assert is_generic_noise(
        title="OpenAI raises $1B for ChatGPT expansion",
        summary_en="AI chatbot gets more funding.",
    ) is True


def test_generative_ai_is_noise():
    assert is_generic_noise(
        title="Startup raises $200M for generative AI platform",
        summary_en="The company builds AI-generated content tools.",
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


def test_source_alone_is_not_sufficient():
    """Security source without cybersecurity content must not pass."""
    assert should_include_in_companies_business(
        event_type="mna",
        title="Acquisition announced",
        summary_en="Deal terms were not disclosed.",
        company_names=["TechCorp"],
        source_name="SecurityWeek",
    ) is False


def test_security_source_plus_content_signal_passes():
    """Source + content signal together should still pass."""
    assert should_include_in_companies_business(
        event_type="mna",
        title="CrowdStrike acquires identity startup",
        summary_en="The cybersecurity company expands its identity protection portfolio.",
        company_names=["CrowdStrike"],
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


def test_whatsapp_product_launch_blocked_even_from_security_source():
    """WhatsApp consumer updates must not reach companies_business."""
    assert should_include_in_companies_business(
        event_type="product_launch",
        title="WhatsApp launches new privacy feature",
        summary_en="The messaging platform adds new settings.",
        company_names=["Meta", "WhatsApp"],
        source_name="SecurityWeek",
    ) is False


def test_generic_meta_corporate_news_blocked():
    """Generic big-tech news with no cybersecurity angle must be blocked."""
    assert should_include_in_companies_business(
        event_type="mna",
        title="Meta acquires AI startup",
        summary_en="Meta is expanding its generative AI capabilities.",
        company_names=["Meta"],
        source_name="Dark Reading",
    ) is False


def test_generic_chatgpt_news_blocked():
    """Generic AI product news without security relevance must be blocked."""
    assert should_include_in_companies_business(
        event_type="funding",
        title="OpenAI raises $500M for ChatGPT expansion",
        summary_en="The AI company plans to scale its large language model.",
        company_names=["OpenAI"],
        source_name="TechCrunch",
    ) is False


def test_real_security_mna_with_vendor_passes():
    """Genuine cybersecurity M&A must still pass."""
    assert should_include_in_companies_business(
        event_type="mna",
        title="Palo Alto Networks acquires cloud security startup",
        summary_en="The acquisition strengthens the company's cloud security portfolio.",
        company_names=["Palo Alto Networks"],
        source_name="Bloomberg",
    ) is True


def test_real_security_earnings_passes():
    """Earnings from a known security vendor must still pass."""
    assert should_include_in_companies_business(
        event_type="earnings",
        title="CrowdStrike reports record Q4 earnings",
        summary_en="CrowdStrike posted strong quarterly results driven by endpoint security growth.",
        company_names=["CrowdStrike"],
        source_name="Reuters",
    ) is True


# ── _company_names_have_security_vendor ──────────────────────────────────────

def test_vendor_present_returns_true():
    assert _company_names_have_security_vendor(["CrowdStrike", "Acme"]) is True


def test_no_vendor_returns_false():
    assert _company_names_have_security_vendor(["OpenAI", "Meta"]) is False


def test_empty_list_returns_false():
    assert _company_names_have_security_vendor([]) is False


def test_none_returns_false():
    assert _company_names_have_security_vendor(None) is False


# ── is_generic_noise — new noise terms ───────────────────────────────────────

def test_openai_is_noise():
    assert is_generic_noise(title="OpenAI raises $5B in funding", summary_en="The AI company plans expansion.") is True


def test_youtube_is_noise():
    assert is_generic_noise(title="YouTube launches new creator monetization", summary_en="Google expands YouTube.") is True


def test_google_gemini_is_noise():
    assert is_generic_noise(title="Google Gemini gets major update", summary_en="The AI model is now faster.") is True


def test_meta_platforms_is_noise():
    assert is_generic_noise(title="Meta Platforms reports strong Q2 earnings", summary_en="Social revenue grew.") is True


def test_iphone_is_noise():
    assert is_generic_noise(title="Apple launches iPhone 17", summary_en="New device features unveiled.") is True


# ── should_include_in_companies_business — new noise-bypass tests ─────────────

def test_openai_funding_with_incidental_security_keyword_blocked():
    """OpenAI funding with an incidental security keyword must still be blocked."""
    assert should_include_in_companies_business(
        event_type="funding",
        title="OpenAI raises $5B",
        summary_en="The company plans to invest in AI safety and authentication systems.",
        company_names=["OpenAI"],
        source_name="TechCrunch",
    ) is False


def test_security_vendor_integration_with_openai_not_blocked():
    """CrowdStrike story mentioning OpenAI must not be blocked by 'openai' noise."""
    assert should_include_in_companies_business(
        event_type="partnership",
        title="CrowdStrike integrates OpenAI models for threat detection",
        summary_en="The cybersecurity company will use OpenAI to enhance its endpoint security platform.",
        company_names=["CrowdStrike"],
        source_name="Dark Reading",
    ) is True


def test_meta_platforms_earnings_blocked():
    """Generic Meta Platforms earnings with no cybersecurity angle must be blocked."""
    assert should_include_in_companies_business(
        event_type="earnings",
        title="Meta Platforms reports record quarterly earnings",
        summary_en="Strong ad revenue driven by Facebook and Instagram growth.",
        company_names=["Meta Platforms"],
        source_name="Bloomberg",
    ) is False


def test_google_gemini_product_launch_blocked():
    """Google Gemini product news must be blocked even with incidental security keyword."""
    assert should_include_in_companies_business(
        event_type="product_launch",
        title="Google Gemini adds new authentication features",
        summary_en="The AI assistant now supports multi-factor authentication.",
        company_names=["Google"],
        source_name="TechCrunch",
    ) is False


def test_iphone_launch_blocked():
    """Apple iPhone news must be blocked."""
    assert should_include_in_companies_business(
        event_type="product_launch",
        title="Apple launches iPhone 17 with enhanced security chip",
        summary_en="The new device includes endpoint security improvements.",
        company_names=["Apple"],
        source_name="TechCrunch",
    ) is False
