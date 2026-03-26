"""
Input/output schemas for the digest-writing LLM stage.

This stage takes a materialized digest entry (with structured facts)
and produces final polished copy in the configured output language.
"""
from pydantic import BaseModel


class DigestEntryInput(BaseModel):
    """Input to the digest-writing LLM call."""
    entry_id: str
    title: str
    event_type: str
    company_names: list[str]
    amount_text: str | None
    currency: str | None
    canonical_summary_en: str | None
    why_it_matters_en: str | None
    output_language: str  # "en" or "ru"


class DigestEntryOutput(BaseModel):
    """Output from the digest-writing LLM call."""
    final_summary: str
    final_why_it_matters: str
