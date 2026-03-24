"""
Deterministic URL canonicalization.

Conservative approach: lowercase scheme/host, remove fragment,
strip well-known tracking-only query parameters.
No path manipulation. Returns original string on any parse error.
"""
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Parameters that exist solely for tracking and carry no content identity.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
    }
)


def canonicalize_url(url: str) -> str:
    """
    Return a canonical form of *url*.

    Transformations applied:
    - Lowercase scheme and host.
    - Remove fragment.
    - Strip tracking-only query parameters (UTM, fbclid, gclid).
    - All other query parameters and the path are left unchanged.

    Returns the original string unchanged if it is empty or cannot be parsed.
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            filtered = {k: v for k, v in params.items() if k not in _TRACKING_PARAMS}
            new_query = urlencode(filtered, doseq=True)
        else:
            new_query = ""

        # fragment="" drops the #anchor; params="" keeps the semicolon-params field empty
        return urlunparse((scheme, netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return url
