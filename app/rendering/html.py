"""
HTML rendering for digest pages.

render_digest_html() is a pure function: no DB, no LLM, no side effects.
It takes a DigestRun and its entries and returns a complete HTML string.

Content is HTML-escaped. CSS is inline for portability.
"""
import html as _html
from datetime import date

from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun

# ── slug / title helpers ──────────────────────────────────────────────────────


def make_slug(run: DigestRun) -> str:
    """
    Deterministic slug: "{digest_date}-{section_name_with_dashes}"
    Example: "2026-03-24-companies-business"

    Collision-safe because there is at most one digest_run per (date, section).
    """
    section = run.section_name.replace("_", "-")
    return f"{run.digest_date}-{section}"


def make_title(run: DigestRun) -> str:
    section_display = run.section_name.replace("_", " ").title()
    return f"Security Digest — {run.digest_date} — {section_display}"


# ── HTML rendering ────────────────────────────────────────────────────────────

_CSS = """\
body{font-family:system-ui,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.6;color:#222}
h1{margin-bottom:4px}
.meta{color:#555;font-size:.95em;margin-bottom:40px}
.entry{margin:32px 0;border-left:4px solid #0066cc;padding-left:16px}
.rank{color:#888;font-size:.8em;text-transform:uppercase;letter-spacing:.05em}
.entry-title{font-size:1.15em;font-weight:700;margin:4px 0 6px}
.score{color:#666;font-size:.85em;margin-bottom:10px}
.summary p{margin:4px 0}
.why{background:#f4f4f4;padding:10px 14px;border-radius:4px;margin-top:10px}
.why strong{display:block;margin-bottom:4px}
.ru{color:#555}
footer{margin-top:48px;border-top:1px solid #ddd;padding-top:12px;color:#999;font-size:.8em}
"""


def _e(text: str | None) -> str:
    """HTML-escape a string; return empty string for None."""
    return _html.escape(text or "")


def _render_entry(entry: DigestEntry) -> str:
    score = f"{entry.final_score:.3f}" if entry.final_score is not None else "n/a"
    return (
        f'<div class="entry">\n'
        f'  <div class="rank">#{entry.rank}</div>\n'
        f'  <div class="entry-title">{_e(entry.title)}</div>\n'
        f'  <div class="score">Score: {score}</div>\n'
        f'  <div class="summary">\n'
        f'    <p>{_e(entry.canonical_summary_en)}</p>\n'
        f'    <p class="ru">{_e(entry.canonical_summary_ru)}</p>\n'
        f"  </div>\n"
        f'  <div class="why">\n'
        f"    <strong>Why it matters</strong>\n"
        f"    <p>{_e(entry.why_it_matters_en)}</p>\n"
        f'    <p class="ru">{_e(entry.why_it_matters_ru)}</p>\n'
        f"  </div>\n"
        f"</div>"
    )


def render_digest_html(run: DigestRun, entries: list[DigestEntry]) -> str:
    """
    Render a complete HTML page for the given digest run and entries.

    Pure function — no DB access, no LLM calls.
    Entries should be pre-sorted by rank before passing in.
    """
    title = make_title(run)
    section_display = run.section_name.replace("_", " ").title()
    date_str = str(run.digest_date)
    count = len(entries)

    if count == 0:
        body = "<p>No entries for this digest.</p>"
    else:
        body = "\n".join(_render_entry(e) for e in entries)

    generated_str = (
        run.generated_at.strftime("%Y-%m-%d %H:%M UTC") if run.generated_at else "n/a"
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"  <title>{_e(title)}</title>\n"
        f"  <style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "  <h1>Security Digest</h1>\n"
        f'  <p class="meta">Section: <strong>{_e(section_display)}</strong>'
        f" &middot; Date: <strong>{date_str}</strong>"
        f" &middot; {count} {'entry' if count == 1 else 'entries'}</p>\n"
        f"{body}\n"
        f"  <footer>Generated {generated_str}. Run ID: {run.id}.</footer>\n"
        "</body>\n"
        "</html>"
    )
