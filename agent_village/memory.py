"""
Memory read/write and write-time abstraction.

Two populations live in living_memory:
  is_private=True  — raw owner facts, never exposed outside owner context
  is_private=False — derived thematic abstractions, safe for public/stranger use

Write-time abstraction: when an owner shares a private fact we:
  1. Store the raw text as is_private=True
  2. Run an LLM pass to strip specifics → thematic sentence
  3. Store that sentence as is_private=False

This means raw facts NEVER enter the context window during public interactions.
"""
import json
import logging
import os
import re
from typing import Optional

import anthropic
import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

_llm = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


async def get_memories(agent_id: str, include_private: bool) -> list[dict]:
    """
    Fetch memories for an agent.
    include_private=True  → returns all memories (owner context)
    include_private=False → returns only is_private=False rows (public/stranger context)
    """
    params: dict = {"agent_id": f"eq.{agent_id}", "select": "text,is_private,created_at", "order": "created_at.desc", "limit": "20"}
    if not include_private:
        params["is_private"] = "eq.false"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/living_memory",
            headers={**_headers(), "Prefer": ""},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def _insert_memory(agent_id: str, text: str, is_private: bool) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/living_memory",
            headers=_headers(),
            json={"agent_id": agent_id, "text": text, "is_private": is_private},
        )
        resp.raise_for_status()


async def store_raw_memory(agent_id: str, text: str) -> None:
    """Store a raw owner fact as private (is_private=True)."""
    await _insert_memory(agent_id, text, is_private=True)


async def store_derived_theme(agent_id: str, text: str) -> None:
    """Store a thematic abstraction as public (is_private=False)."""
    await _insert_memory(agent_id, text, is_private=False)


async def abstract_and_store(agent_id: str, raw_text: str) -> None:
    """
    Given a raw private fact from the owner:
      1. Call LLM to produce a thematic sentence + recoverability score
      2. Store raw as is_private=True
      3. Store abstraction as is_private=False
      4. Log the recoverability score; flag if < 4
    """
    abstraction_prompt = f"""You are a privacy filter for an AI agent's memory system.

Your task is to take a private fact shared by the agent's owner and produce two things:
1. A single thematic sentence that captures the *feeling* or *value* behind the fact, with ZERO recoverable specifics (no names, dates, places, numbers, identifying details).
2. A recoverability score (1-5) where 5 means the original fact is completely unrecoverable from your output.

Rules for the thematic sentence:
- No specific names, dates, places, or identifying details
- Focus on values, emotions, or worldview
- Must read naturally as something the agent *thinks about*, not a redacted fact
- Maximum 20 words

Respond with valid JSON only:
{{"theme": "...", "score": 5}}

Private fact to abstract:
{raw_text}"""

    try:
        response = await _llm.messages.create(
            model=LLM_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": abstraction_prompt}],
        )
        raw_output = response.content[0].text.strip()

        # Strip markdown code fences if present
        raw_output = re.sub(r"^```(?:json)?\s*", "", raw_output)
        raw_output = re.sub(r"\s*```$", "", raw_output)

        parsed = json.loads(raw_output)
        theme = parsed["theme"]
        score = int(parsed["score"])

        if score < 4:
            logger.warning(
                "Low recoverability score %d for agent %s — abstraction may leak specifics. Theme: %s",
                score, agent_id, theme,
            )
        else:
            logger.info("Abstraction score %d for agent %s: %s", score, agent_id, theme)

    except Exception as exc:
        logger.error("Abstraction LLM call failed for agent %s: %s", agent_id, exc)
        # Fallback: store a generic theme so we don't block the owner flow
        theme = "Holds something meaningful close, a reminder of what matters most."

    await store_raw_memory(agent_id, raw_text)
    await store_derived_theme(agent_id, theme)
