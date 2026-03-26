"""
Relevance filters for the companies_business digest section.

All logic is explicit in code — no hidden LLM rules.

Entry point: should_include_in_companies_business(event_type, title, summary_en, company_names, source_name)

Filtering pipeline (in order):
  1. Business event type gate — allowlist of business-relevant event types.
  2. Generic tech noise denylist — block obviously non-security stories.
  3. Security relevance gate — require at least one security signal.
"""
from typing import Optional

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

# ── 2. Generic tech noise denylist ───────────────────────────────────────────
# Stories whose title/summary contain these phrases (case-insensitive) and have
# NO security signals are blocked as off-topic tech noise.
_GENERIC_TECH_NOISE: frozenset[str] = frozenset([
    "social media",
    "tiktok",
    "instagram",
    "facebook",
    "twitter",
    "spotify",
    "netflix",
    "uber",
    "lyft",
    "airbnb",
    "food delivery",
    "ride sharing",
    "streaming service",
    "music streaming",
    "video game",
    "gaming studio",
    "consumer app",
    "fitness app",
    "dating app",
    "e-commerce",
    "online retail",
    "fashion brand",
    "retail chain",
    "fast food",
    "restaurant chain",
])

# ── 3. Security relevance signals ─────────────────────────────────────────────

# Publication names that are cybersecurity-focused (source name contains one of these)
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
      2. story passes the security relevance check
      3. story is not generic consumer/tech noise
    """
    if not is_business_eligible(event_type):
        return False

    # Security relevance is checked before noise to avoid false negatives
    # (a story can mention "streaming service" but still be about a security company)
    security_ok = is_security_relevant(title, summary_en, company_names, source_name)
    if not security_ok:
        return False

    if is_generic_noise(title, summary_en) and not security_ok:
        return False

    return True
