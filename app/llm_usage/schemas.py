"""
Shared dataclass returned by all LLM boundary functions alongside their result.

Usage:
    result, usage = extract_facts_llm(story_input)
    result, usage = assess_cluster_llm(cluster_input)
    result, usage = write_digest_entry_llm(entry_input)

usage is always an LlmUsageInfo — never None.
If the real token counts are unavailable, pass zeros.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class LlmUsageInfo:
    model_name: str
    input_tokens: int
    output_tokens: int
    # UUID string of the object this call was performed for (story_id, cluster_id, entry_id)
    related_object_id: Optional[str] = None
