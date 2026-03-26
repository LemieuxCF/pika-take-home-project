"""
Agent loading and identity management.
Handles fetching agent data from Supabase and owner verification.
"""
import os
import httpx
from typing import Optional

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def get_agent(agent_id: str) -> Optional[dict]:
    """Fetch a single agent row by ID. Returns None if not found."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/living_agents",
            headers=_headers(),
            params={"id": f"eq.{agent_id}", "select": "*"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None


async def list_agents() -> list[dict]:
    """Fetch all agents — used by the scheduler."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/living_agents",
            headers=_headers(),
            params={"select": "*"},
        )
        resp.raise_for_status()
        return resp.json()


def verify_owner(agent: dict, owner_key: str) -> bool:
    """Return True if owner_key matches the agent's stored owner_key."""
    stored = agent.get("owner_key")
    if not stored:
        return False
    return stored == owner_key
