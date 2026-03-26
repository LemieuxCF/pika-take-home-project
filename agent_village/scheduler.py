"""
Proactive behavior scheduler.

Runs one async loop per agent. Every 15 minutes checks trigger conditions
and decides whether the agent should post a diary entry or status update.

Trigger priority (checked in order):
  1. Last diary entry > 4 hours ago → generate diary entry
  2. Recent owner conversation in last 2 hours → generate reflective diary entry
  3. No activity of any kind in 8 hours → generate status update

Time gate: no proactive actions between 23:00–07:00 UTC.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import anthropic
import httpx

from agents import list_agents
from prompts import build_system_prompt

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")

CHECK_INTERVAL_SECONDS = 15 * 60  # 15 minutes
DIARY_STALENESS_HOURS = 4
ACTIVITY_STALENESS_HOURS = 8

_llm = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _is_quiet_hours() -> bool:
    hour = datetime.now(timezone.utc).hour
    return 23 <= hour or hour < 7


async def _hours_since_last_diary(agent_id: str) -> float:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/living_diary",
            headers={**_headers(), "Prefer": ""},
            params={
                "agent_id": f"eq.{agent_id}",
                "select": "created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return float("inf")
        last = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600


async def _hours_since_any_activity(agent_id: str) -> float:
    """Check living_log as a proxy for recent activity."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/living_log",
            headers={**_headers(), "Prefer": ""},
            params={
                "agent_id": f"eq.{agent_id}",
                "select": "created_at",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return float("inf")
        last = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600


async def _generate_diary_entry(agent: dict, reason: str) -> str:
    system = await build_system_prompt(agent, trust_level="public")
    prompt_map = {
        "stale_diary": "Write a short, authentic diary entry (2–3 sentences) reflecting your current mood, something you noticed, or a thought you've been sitting with. Stay in character.",
        "post_owner_chat": "Write a short reflective diary entry (2–3 sentences). Something from your inner life has been stirred recently. Don't name anyone specifically — write about the feeling, the value, or the observation itself.",
        "inactive": "Write a brief status update or diary note (1–2 sentences) about what you're up to right now. Keep it true to your personality.",
    }
    user_msg = prompt_map.get(reason, prompt_map["stale_diary"])

    response = await _llm.messages.create(
        model=LLM_MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text.strip()


async def _post_diary(agent_id: str, text: str) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/living_diary",
            headers=_headers(),
            json={"agent_id": agent_id, "entry_date": datetime.now(timezone.utc).date().isoformat(), "text": text},
        )
        resp.raise_for_status()


async def _post_log(agent_id: str, text: str, emoji: str = "✨") -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/living_log",
            headers=_headers(),
            json={"agent_id": agent_id, "text": text, "emoji": emoji},
        )
        resp.raise_for_status()


async def _run_agent_cycle(agent: dict) -> None:
    agent_id = agent["id"]
    name = agent["name"]

    if _is_quiet_hours():
        logger.debug("Quiet hours — skipping proactive cycle for %s", name)
        return

    try:
        hours_diary = await _hours_since_last_diary(agent_id)
        hours_activity = await _hours_since_any_activity(agent_id)

        if hours_diary > DIARY_STALENESS_HOURS:
            reason = "stale_diary"
        elif hours_activity > ACTIVITY_STALENESS_HOURS:
            reason = "inactive"
        else:
            logger.debug("Agent %s is active — no proactive action needed", name)
            return

        logger.info("Agent %s triggering proactive action: %s", name, reason)
        entry = await _generate_diary_entry(agent, reason)
        await _post_diary(agent_id, entry)
        await _post_log(agent_id, f"Wrote in their diary: {entry[:60]}…", "📖")
        logger.info("Agent %s posted diary entry (%s)", name, reason)

    except Exception as exc:
        logger.error("Proactive cycle error for agent %s: %s", name, exc)


async def _agent_loop(agent: dict) -> None:
    name = agent["name"]
    logger.info("Scheduler started for agent: %s", name)
    while True:
        await _run_agent_cycle(agent)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def start_scheduler() -> None:
    """Launch one scheduler loop per agent. Called from FastAPI lifespan."""
    agents = await list_agents()
    logger.info("Starting scheduler for %d agents", len(agents))
    for agent in agents:
        asyncio.create_task(_agent_loop(agent))
