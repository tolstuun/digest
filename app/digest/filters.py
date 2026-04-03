"""
Relevance filters for digest sections.

All logic is explicit in code — no hidden LLM rules.

Sections implemented here:
  companies_business — funding, M&A, earnings, market moves of cybersecurity vendors.
  product_updates    — meaningful cybersecurity product launches and major feature releases.
  incidents          — ransomware attacks, breaches, leaks, extortion, major security incidents.

companies_business pipeline (in order):
  1. Business event type gate — allowlist of business-relevant event types.
  2. Content security signal — keyword or vendor in story text/company list.
     Source name alone is NOT sufficient; the story itself must carry the signal.
  3. Generic tech/consumer noise denylist — block off-topic stories even when
     a security keyword happens to appear incidentally.

product_updates pipeline (in order):
  1. Event type gate — product_launch only.
  2. Positive product signal — explicit launch/release/capability keyword required.
  3. Trivial-update denylist — minor updates, bug fixes, patches, UI tweaks blocked.
  4. Content security signal — same as companies_business (keyword or vendor).
  5. Generic tech/consumer noise denylist — same as companies_business.

incidents pipeline (in order):
  1. At least one explicit incident keyword required in title/summary/source.
     No event-type gate — incident articles rarely have a uniform event type.

DB-aware helpers:
  cluster_passes_companies_business_gate(db, cluster)
  cluster_passes_product_updates_gate(db, cluster)
  cluster_passes_incidents_gate(db, cluster)
  All load rep story/facts/source from the database and delegate to the
  appropriate should_include_* function.  Used to gate expensive LLM stages.
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
    # ── chips / semiconductors / generic AI infrastructure ────────────────────
    # These categories rarely carry genuine cybersecurity business news.
    # Stories with an incidental security keyword (e.g. "authentication") but
    # primarily about chip design / AI compute are blocked here.
    "ai chip",
    "ai chips",
    "semiconductor",
    "semiconductors",
    "gpu ",
    "gpus",
    "inference chip",
    "inference chips",
    "compute chip",
    "foundation model",
    "llm platform",
    # ── consumer content generation ───────────────────────────────────────────
    "music generation",
    "video generation",
    "image generation",
    # ── productivity / collaboration tools ────────────────────────────────────
    "meeting assistant",
    "note taker",
    "notetaker",
    "productivity app",
    # ── consumer devices ─────────────────────────────────────────────────────
    "smartphone",
    "handset",
    # ── adtech / martech / ecommerce ─────────────────────────────────────────
    "adtech",
    "martech",
    "ad tech",
    "mar tech",
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
    # ── OpenAI / non-security AI companies ────────────────────────────────────
    # "openai" alone is blocked; security-vendor integration stories
    # (e.g. "CrowdStrike integrates OpenAI") are exempt via the vendor bypass
    # in should_include_in_companies_business.
    "openai",
    # ── Google consumer / AI-product noise ────────────────────────────────────
    "youtube",
    "google gemini",
    "google deepmind",
    "google bard",
    "google assistant",
    "google pixel",
    "google chrome",
    "google ads",
    "google search",
    "gmail",
    # ── Meta corporate / consumer products ────────────────────────────────────
    "meta platforms",
    "oculus",
    # ── Apple consumer devices / OS ───────────────────────────────────────────
    "iphone",
    "ipad",
    "apple watch",
    "ios ",
    "macos ",
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
    "data security", "email security", "browser security",
    "devsecops", "appsec", "soc ", "siem", "xdr", "edr", "mdr",
    # threat terms
    "ransomware", "malware", "phishing", "vulnerability", "exploit", "zero-day",
    "zero day", "breach", "hack", "threat intel", "threat detection",
    "threat intelligence", "threat actor", "threat landscape",
    "cyberattack", "cyber attack",
    "incident response", "penetration test", "pentest", "red team", "blue team",
    # identity and access
    "authentication", "identity management", "identity and access",
    "access management", "iam ", "pam ", "privileged access",
    # network / perimeter
    "zero trust", "ztna", "firewall", "intrusion detection", "ids ", "ips ",
    "sase ", "sse ", "casb ",
    # posture / exposure
    "attack surface", "exposure management", "security posture",
    "vulnerability management", "patch management",
    # operations
    "security operations", "security orchestration", "soar ", "threat hunting",
    "security platform", "security vendor",
    # compliance/regulatory in security context
    "data protection", "privacy regulation", "gdpr", "ccpa", "hipaa",
    "sox compliance", "pci dss", "fedramp",
    # other categories
    "antivirus", "anti-virus", "endpoint protection",
    "deception technology", "secure access",
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


def _company_names_have_security_vendor(company_names: Optional[list[str]]) -> bool:
    """Return True if any entry in company_names matches a known security vendor hint."""
    if not company_names:
        return False
    companies_str = " ".join(company_names).lower()
    return any(hint in companies_str for hint in _SECURITY_VENDOR_HINTS)


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
    #
    # Exception: bypass the noise check when a known security vendor is listed
    # in company_names — the story is genuinely about that vendor even if a
    # consumer brand is mentioned incidentally (e.g. "CrowdStrike integrates
    # OpenAI for threat detection" should not be blocked by "openai" noise).
    if not _company_names_have_security_vendor(company_names):
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


# ══════════════════════════════════════════════════════════════════════════════
# product_updates section
# ══════════════════════════════════════════════════════════════════════════════

# Positive product-launch signal — at least one of these must appear in
# title or summary to qualify a story as a meaningful product event.
_PRODUCT_LAUNCH_SIGNALS: frozenset[str] = frozenset([
    "launches", "launch", "launched",
    "introduces", "introduced",
    "unveils", "unveiled",
    "releases", "released", "release",
    "announces", "announced",          # only when paired with a product noun below
    "new platform", "new product", "new solution", "new module", "new capability",
    "new feature",
    "expands platform", "platform expansion",
    "adds capability", "adds support", "adds integration",
    "general availability", "ga release", "now available",
    "integration with",                # meaningful integrations count
])

# Trivial-update denylist — any of these phrases indicate a minor, non-newsworthy
# update that should not appear in the product_updates section.
_TRIVIAL_UPDATE_SIGNALS: frozenset[str] = frozenset([
    "minor update",
    "bug fix", "bugfix", "bug fixes",
    "patch release", "security patch", "hotfix", "hot fix",
    "ui improvement", "ui tweak", "ui change",
    "cosmetic change", "cosmetic update",
    "documentation update", "docs update",
    "version bump",
])


def _has_product_launch_signal(title: Optional[str], summary_en: Optional[str]) -> bool:
    """Return True if the story text contains at least one positive product-launch term."""
    combined = f"{title or ''} {summary_en or ''}".strip()
    if not combined:
        return False
    return _text_contains_any(combined, _PRODUCT_LAUNCH_SIGNALS)


def is_trivial_update(title: Optional[str], summary_en: Optional[str]) -> bool:
    """Return True if the story is a minor/trivial update that should be excluded."""
    combined = f"{title or ''} {summary_en or ''}".strip()
    if not combined:
        return False
    return _text_contains_any(combined, _TRIVIAL_UPDATE_SIGNALS)


def should_include_in_product_updates(
    event_type: Optional[str],
    title: Optional[str],
    summary_en: Optional[str],
    company_names: Optional[list[str]],
    source_name: Optional[str],
) -> bool:
    """
    Combined relevance gate for the product_updates section.

    Returns True only when:
      1. event_type is product_launch
      2. title/summary contains an explicit product launch/release signal
      3. title/summary does NOT contain a trivial-update signal
      4. the story content carries an explicit cybersecurity signal
         (keyword or known vendor — source name alone is not sufficient)
      5. the story is not generic consumer/tech noise
         (unless a known security vendor is in company_names)
    """
    if (event_type or "").lower() != "product_launch":
        return False

    if not _has_product_launch_signal(title, summary_en):
        return False

    if is_trivial_update(title, summary_en):
        return False

    if not _has_content_security_signal(title, summary_en, company_names):
        return False

    if not _company_names_have_security_vendor(company_names):
        if is_generic_noise(title, summary_en):
            return False

    return True


def cluster_passes_product_updates_gate(db: "Session", cluster: "EventCluster") -> bool:
    """
    Load cluster data from the database and run should_include_in_product_updates.

    Used before expensive LLM stages to skip clusters that will not survive
    the product_updates relevance gate at assembly time.
    """
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

    return should_include_in_product_updates(
        event_type=cluster.event_type,
        title=rep_story.title if rep_story else None,
        summary_en=rep_facts.canonical_summary_en if rep_facts else None,
        company_names=rep_facts.company_names if rep_facts else None,
        source_name=source_name,
    )


# ══════════════════════════════════════════════════════════════════════════════
# incidents section
# ══════════════════════════════════════════════════════════════════════════════

# At least one of these must appear in title, summary, or source name.
# Covers: ransomware victims, breaches, leaks, extortion, major incidents.
_INCIDENT_KEYWORDS: frozenset[str] = frozenset([
    # ransomware / extortion
    "ransomware",
    "ransom",
    "extortion",
    "double extortion",
    "data extortion",
    "lockbit",
    "blackcat",
    "alphv",
    "clop",
    "cl0p",
    "akira",
    "play ransomware",
    "royal ransomware",
    "black basta",
    "rhysida",
    "medusa ransomware",
    # breach / compromise
    "breach",
    "breached",
    "data breach",
    "security breach",
    "compromised",
    "compromise",
    "hacked",
    "hacking",
    "cyberattack",
    "cyber attack",
    "unauthorized access",
    "intrusion",
    # leak / exfiltration / theft
    "data leak",
    "data leaked",
    "leaked",
    "exfiltration",
    "exfiltrated",
    "data theft",
    "stolen data",
    "stolen credentials",
    "credential theft",
    "data exposure",
    "exposed data",
    "sensitive data",
    # generic incident / victim language
    "victim",
    "victims",
    "claimed attack",
    "claimed responsibility",
    "posted on",           # dark web leak site post patterns
    "listed on",
    "attacked by",
    "hit by",
    "targeted by",
    "incident response",
    "security incident",
])

# Known incident-focused source names — if the source is one of these,
# any story is presumed to be an incident report.
_INCIDENT_SOURCES: frozenset[str] = frozenset([
    "ransomware.live",
    "bleepingcomputer",
    "databreaches.net",
    "haveibeenpwned",
    "vx-underground",
])


def should_include_in_incidents(
    title: Optional[str],
    summary_en: Optional[str],
    source_name: Optional[str],
) -> bool:
    """
    Relevance gate for the incidents section.

    Returns True when the story clearly describes a ransomware attack, data
    breach, exfiltration, extortion, or major security incident.

    No event-type gate — incident articles come from diverse sources and rarely
    have a uniform event_type.  The content signal is sufficient.

    Rule: at least one incident keyword in (title + summary), OR source is a
    known incident-tracking feed.
    """
    # Known incident source passes unconditionally
    if source_name and any(hint in source_name.lower() for hint in _INCIDENT_SOURCES):
        return True

    combined = f"{title or ''} {summary_en or ''}".strip().lower()
    if not combined:
        return False

    return any(kw in combined for kw in _INCIDENT_KEYWORDS)


def cluster_passes_incidents_gate(db: "Session", cluster: "EventCluster") -> bool:
    """
    Load cluster data from the database and run should_include_in_incidents.

    Used before expensive LLM stages to skip clusters that will not survive
    the incidents relevance gate at assembly time.
    """
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

    return should_include_in_incidents(
        title=rep_story.title if rep_story else None,
        summary_en=rep_facts.canonical_summary_en if rep_facts else None,
        source_name=source_name,
    )


def cluster_passes_any_section_gate(db: "Session", cluster: "EventCluster") -> bool:
    """
    Return True if the cluster passes at least one known section gate.

    Used in orchestration to decide whether to run expensive LLM assessment
    on a cluster.  A cluster that can't pass any gate is skipped.
    """
    return (
        cluster_passes_companies_business_gate(db, cluster)
        or cluster_passes_product_updates_gate(db, cluster)
        or cluster_passes_incidents_gate(db, cluster)
    )
