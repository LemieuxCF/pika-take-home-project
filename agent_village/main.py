"""
FastAPI backend for Agent Village.

Routes:
  POST /chat/{agent_id}          — conversation with an agent (owner or stranger)
  POST /agents/{agent_id}/diary  — manually trigger a diary entry (for demo)
  GET  /agents                   — list all agents
  GET  /agents/{agent_id}        — single agent info
  GET  /health                   — health check
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

from agents import get_agent, list_agents, verify_owner
from memory import abstract_and_store
from prompts import build_system_prompt
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
_llm = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_scheduler()
    yield


app = FastAPI(title="Agent Village", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    owner_key: Optional[str] = None
    conversation_history: list[Message] = []


class ChatResponse(BaseModel):
    reply: str
    trust_level: str
    agent_name: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/agents")
async def get_agents():
    return await list_agents()


@app.get("/agents/{agent_id}")
async def get_agent_info(agent_id: str):
    agent = await get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    # Strip owner_key from response
    agent.pop("owner_key", None)
    return agent


@app.post("/chat/{agent_id}", response_model=ChatResponse)
async def chat(agent_id: str, req: ChatRequest):
    agent = await get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Determine trust level
    if req.owner_key and verify_owner(agent, req.owner_key):
        trust_level = "owner"
    else:
        trust_level = "stranger"

    logger.info("Chat with agent %s — trust_level=%s", agent["name"], trust_level)

    # Build system prompt
    system = await build_system_prompt(agent, trust_level)

    # Assemble message history
    messages = [{"role": m.role, "content": m.content} for m in req.conversation_history]
    messages.append({"role": "user", "content": req.message})

    # Call LLM
    try:
        response = await _llm.messages.create(
            model=LLM_MODEL,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        reply = response.content[0].text.strip()
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM call failed")

    # If owner conversation — scan message for private facts and abstract+store
    if trust_level == "owner":
        # Simple heuristic: store if the message contains personal information keywords
        private_keywords = [
            "birthday", "anniversary", "wife", "husband", "partner", "daughter",
            "son", "mother", "father", "sister", "brother", "friend", "address",
            "phone", "secret", "private", "loves", "hates", "afraid", "allergic",
            "medical", "doctor", "salary", "password",
        ]
        msg_lower = req.message.lower()
        if any(kw in msg_lower for kw in private_keywords):
            logger.info("Detected private fact from owner — abstracting and storing")
            try:
                await abstract_and_store(agent["id"], req.message)
            except Exception as exc:
                logger.error("abstract_and_store failed: %s", exc)

    return ChatResponse(reply=reply, trust_level=trust_level, agent_name=agent["name"])


@app.post("/agents/{agent_id}/diary")
async def trigger_diary(agent_id: str, owner_key: Optional[str] = None):
    """Manually trigger a proactive diary entry. Useful for demos."""
    from scheduler import _generate_diary_entry, _post_diary, _post_log

    agent = await get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Require owner_key or accept open for demo purposes
    # In production this would be owner-only
    entry = await _generate_diary_entry(agent, reason="stale_diary")
    await _post_diary(agent_id, entry)
    await _post_log(agent_id, f"Wrote in their diary: {entry[:60]}…", "📖")

    return {"agent": agent["name"], "diary_entry": entry}
