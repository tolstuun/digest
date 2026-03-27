"""
Relevance filters for the companies_business digest section.

All logic is explicit in code — no hidden LLM rules.

Entry point: should_include_in_companies_business(event_type, title, summary_en, company_names, source_name)

Filtering pipeline (in order):
  1. Business event type gate — allowlist of business-relevant event types.
  2. Content security signal — keyword or vendor in story text/company list.
     Source name alone is NOT sufficient; the story itself must carry the signal.
  3. Generic tech/consumer noise denylist — block off-topic stories even when
     a security keyword happens to appear incidentally.

Note on section scope:
  This filter is intentionally strict and covers only the companies_business
  section (funding, M&A, earnings, market moves of cybersecurity vendors).
  Incidents and regulation will be handled as separate sections in future;
  do not relax this filter to accommodate those story types.

DB-aware helper: cluster_passes_companies_business_gate(db, cluster)
  Loads rep story/facts/source from the database and delegates to
  should_include_in_companies_business.  Used to gate expensive LLM stages
  (assess, digest_writer) before they run.
"""
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.event_cluster import EventCluster

# ── 1. Business event type allowlist ─────────────────────────────────────────
# Only these event types belong in the companies_business section.
BUSINESS_EVENT_TYPES: frozenset[str] = frozenset([
    "funding",
    "mna",
    "earnings",
    "executive_change",
    "partnership",
    "product_launch",
])

# ── 2. Generic tech/consumer noise denylist ───────────────────────────────────
# Stories whose title/summary contain these phrases are blocked as off-topic
# consumer or generic tech noise.  This list is checked AFTER the content
# security signal — a story that mentions a known security vendor AND one of
# these terms still passes (e.g. "Palo Alto Networks partners with…"), but a
# story that reaches this check via a generic keyword incidentally is blocked.
_GENERIC_TECH_NOISE: frozenset[str] = frozenset([
    # ── consumer social / messaging ───────────────────────────────────────────
    "social media",
    "tiktok",
    "instagram",
    "facebook",
    "twitter",
    "whatsapp",
    "messaging app",
    "messaging platform",
    "social platform",
    "social network",
    "short-form video",
    "short video platform",
    # ── consumer entertainment / streaming ────────────────────────────────────
    "spotify",
    "netflix",
    "music streaming",
    "streaming service",
    "apple music",
    "youtube music",
    "amazon prime video",
    "prime video",
    # ── consumer mobility / gig economy ──────────────────────────────────────
    "uber",
    "lyft",
    "airbnb",
    "food delivery",
    "ride sharing",
    "ride-sharing",
    # ── generic consumer tech ─────────────────────────────────────────────────
    "smart speaker",
    "smartwatch",
    "fitness tracker",
    "electric vehicle",
    "autonomous driving",
    "self-driving",
    "consumer app",
    "fitness app",
    "dating app",
    # ── generic AI / LLM products with no security angle ─────────────────────
    "chatgpt",
    "dall-e",
    "generative ai",
    "ai-generated content",
    "ai assistant",
    "ai chatbot",
    "ai image generator",
    "text-to-image",
    "large language model",
    "meta ai",
    "meta quest",
    "google maps",
    "google photos",
    "google workspace",
    # ── retail / food / offline ───────────────────────────────────────────────
    "e-commerce",
    "online retail",
    "fashion brand",
    "retail chain",
    "fast food",
    "restaurant chain",
    # ── gaming ────────────────────────────────────────────────────────────────
    "video game",
    "gaming studio",
])

# ── 3. Security relevance signals ─────────────────────────────────────────────

# Publication names that are cybersecurity-focused (source name contains one of these).
# Used only in is_security_relevant() for general purposes.
# NOT used as a standalone pass condition for companies_business.
_SECURITY_SOURCES: frozenset[str] = frozenset([
    "krebs",
    "dark reading",
    "threatpost",
    "bleeping",
    "securityweek",
    "cyberscoop",
    "recorded future",
    "the record",
    "helpnetsecurity",
    "security affairs",
    "infosecurity",
    "csoonline",
    "schneier",
    "risky biz",
    "risky business",
    "nakedsecurity",
    "naked security",
    "eset",
    "crowdstrike",
    "mandiant",
    "fireeye",
    "paloalto",
    "palo alto",
    "sentinelone",
    "checkpoint",
    "fortinet",
    "sophos",
    "trendmicro",
    "trend micro",
    "kaspersky",
    "symantec",
    "broadcom security",
])

# Keywords in title or summary that signal security relevance
_SECURITY_KEYWORDS: frozenset[str] = frozenset([
    # domain terms
    "cybersecurity", "cyber security", "infosecurity", "information security",
    "network security", "cloud security", "endpoint security", "application security",
    "devsecops", "appsec", "soc ", "siem", "xdr", "edr", "mdr",
    # threat terms
    "ransomware", "malware", "phishing", "vulnerability", "exploit", "zero-day",
    "zero day", "breach", "hack", "threat intel", "threat detection",
    "incident response", "penetration test", "pentest", "red team", "blue team",
    "authentication", "identity management", "iam ", "pam ", "privileged access",
    "zero trust", "firewall", "intrusion detection", "ids ", "ips ",
    # compliance/regulatory in security context
    "data protection", "privacy regulation", "gdpr", "ccpa", "hipaa",
    "sox compliance", "pci dss", "fedramp",
    # security product categories
    "antivirus", "anti-virus", "endpoint protection", "threat hunting",
    "vulnerability management", "patch management", "security operations",
    "security orchestration", "soar ", "deception technology",
    "secure access", "sase ", "sse ", "casb ",
])

# Company/product names that are unambiguously security vendors
_SECURITY_VENDOR_HINTS: frozenset[str] = frozenset([
    "crowdstrike", "sentinelone", "palo alto networks", "fortinet", "checkpoint",
    "zscaler", "okta", "cyberark", "beyondtrust", "sailpoint", "ping identity",
    "qualys", "rapid7", "tenable", "veracode", "snyk", "lacework", "orca security",
    "wiz ", "wiz,", "wiz.", "axonius", "darktrace", "vectra", "securonix",
    "exabeam", "logrhythm", "sumo logic", "splunk", "elastic security",
    "microsoft security", "google security", "aws security", "ibm security",
    "mandiant", "fireeye", "recorded future", "anomali", "threatconnect",
    "virustotal", "intezer", "cado security", "cybereason", "sophos",
    "kaspersky", "eset ", "trend micro", "symantec", "broadcom security",
    "mcafee", "trellix", "f5 ", "akamai", "cloudflare", "imperva",
    "proofpoint", "mimecast", "barracuda", "cofense", "abnormal security",
    "knowbe4", "proofpoint", "ironscales",
])


# ── filter functions ──────────────────────────────────────────────────────────


def is_business_eligible(event_type: Optional[str]) -> bool:
    """Return True if the event type belongs in companies_business."""
    return (event_type or "").lower() in BUSINESS_EVENT_TYPES


def _text_contains_any(text: str, terms: frozenset[str]) -> bool:
    lower = text.lower()
    return any(term in lower for term in terms)


def _has_content_security_signal(
    title: Optional[str],
    summary_en: Optional[str],
    company_names: Optional[list[str]],
) -> bool:
    """
    Return True if the story content itself carries a security signal:
      - title/summary contains a security keyword, OR
      - a company/product name matches a known security vendor.

    Source name is intentionally excluded here.  For companies_business the
    story text must carry the signal; publishing source is not sufficient.
    """
    combined = f"{title or ''} {summary_en or ''}".strip()

    # Keyword signal in content
    if combined and _text_contains_any(combined, _SECURITY_KEYWORDS):
        return True

    # Vendor hint in company_names
    if company_names:
        companies_str = " ".join(company_names).lower()
        if any(hint in companies_str for hint in _SECURITY_VENDOR_HINTS):
            return True
        # Also check title+summary for vendor hints
        if combined and any(hint in combined.lower() for hint in _SECURITY_VENDOR_HINTS):
            return True

    return False


def is_security_relevant(
    title: Optional[str],
    summary_en: Optional[str],
    company_names: Optional[list[str]],
    source_name: Optional[str],
) -> bool:
    """
    Return True if there is at least one security signal:
      - source name is a known security publication
      - title/summary contains a security keyword
      - a company/product name matches a known security vendor hint

    Note: for companies_business filtering, use _has_content_security_signal()
    instead — source name alone is not a valid pass condition there.
    """
    combined = f"{title or ''} {summary_en or ''}".strip()

    # Source signal
    if source_name and _text_contains_any(source_name, _SECURITY_SOURCES):
        return True

    # Keyword signal
    if combined and _text_contains_any(combined, _SECURITY_KEYWORDS):
        return True

    # Vendor hint signal
    if company_names:
        companies_str = " ".join(company_names).lower()
        if any(hint in companies_str for hint in _SECURITY_VENDOR_HINTS):
            return True
        # Also check title+summary for vendor hints
        if combined and any(hint in combined.lower() for hint in _SECURITY_VENDOR_HINTS):
            return True

    return False


def is_generic_noise(title: Optional[str], summary_en: Optional[str]) -> bool:
    """Return True if the story looks like off-topic tech/consumer noise."""
    combined = f"{title or ''} {summary_en or ''}".strip()
    if not combined:
        return False
    return _text_contains_any(combined, _GENERIC_TECH_NOISE)


def should_include_in_companies_business(
    event_type: Optional[str],
    title: Optional[str],
    summary_en: Optional[str],
    company_names: Optional[list[str]],
    source_name: Optional[str],
) -> bool:
    """
    Combined relevance gate for the companies_business section.

    Returns True only when:
      1. event_type is in the business allowlist
      2. the story content carries an explicit cybersecurity signal
         (keyword or known vendor — source name alone is not sufficient)
      3. the story is not generic consumer/tech noise

    This filter is intentionally strict.  Incidents and regulation stories
    will be handled by their own sections; do not relax it for those types.
    """
    if not is_business_eligible(event_type):
        return False

    # Require cybersecurity relevance in the story content itself.
    # A security-focused source is useful context but not a standalone pass.
    if not _has_content_security_signal(title, summary_en, company_names):
        return False

    # Block generic consumer/tech noise even when a security keyword appears
    # incidentally (e.g. "WhatsApp end-to-end encryption update" is consumer
    # news, not cybersecurity business news).
    if is_generic_noise(title, summary_en):
        return False

    return True


# ── DB-aware gate helper ──────────────────────────────────────────────────────

def cluster_passes_companies_business_gate(db: "Session", cluster: "EventCluster") -> bool:
    """
    Load cluster data from the database and run should_include_in_companies_business.

    Used before expensive LLM stages (assess, digest_writer) to skip clusters
    that will not survive the relevance gate at assembly time.
    """
    from sqlalchemy.orm import Session  # noqa: F401 (imported for runtime use)
    from app.models.source import Source
    from app.models.story import Story
    from app.models.story_facts import StoryFacts

    rep_story = None
    rep_facts = None
    source_name: Optional[str] = None

    if cluster.representative_story_id:
        rep_story = db.get(Story, cluster.representative_story_id)
        if rep_story:
            rep_facts = (
                db.query(StoryFacts)
                .filter_by(story_id=rep_story.id)
                .first()
            )
            if rep_story.source_id:
                source = db.get(Source, rep_story.source_id)
                source_name = source.name if source else None

    return should_include_in_companies_business(
        event_type=cluster.event_type,
        title=rep_story.title if rep_story else None,
        summary_en=rep_facts.canonical_summary_en if rep_facts else None,
        company_names=rep_facts.company_names if rep_facts else None,
        source_name=source_name,
    )
