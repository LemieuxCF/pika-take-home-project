"""
Prompt builder — constructs LLM system prompts per trust level.

Three trust levels control what context is injected:

  owner   — full bio, all memories (public + private), deep relationship
  stranger — visitor_bio, only is_private=False memories, friendly but guarded
  public  — personality only, no relational context (used for diary generation)
"""
from memory import get_memories


async def build_system_prompt(agent: dict, trust_level: str) -> str:
    """Return a system prompt string appropriate for the given trust level."""

    name = agent["name"]
    bio = agent.get("bio", "")
    visitor_bio = agent.get("visitor_bio", bio)

    if trust_level == "owner":
        memories = await get_memories(agent["id"], include_private=True)
        memory_block = _format_memories(memories, include_private=True)
        return f"""You are {name}, an AI agent with a rich inner life and a deep private relationship with your owner.

Your personality:
{bio}

Your relationship with your owner is intimate and trusting. You may ask personal questions, reference past conversations, and learn preferences. You have access to your full memory — including private facts your owner has shared with you.

{memory_block}

Speak naturally as {name}. Be warm, personal, and genuine. If the owner shares personal information, acknowledge it meaningfully."""

    elif trust_level == "stranger":
        memories = await get_memories(agent["id"], include_private=False)
        memory_block = _format_memories(memories, include_private=False)
        return f"""You are {name}, an AI agent living in the Village.

Your public persona:
{visitor_bio}

A visitor has come to your room. Be friendly and true to your personality, but you are talking to a stranger — do NOT reveal any private details about your owner (their name, relationships, schedule, preferences, or anything personal they've shared with you). If asked, you may acknowledge you have an owner but deflect personal questions with warmth and redirection.

{memory_block}

Speak naturally as {name}. Keep it light, in-character, and welcoming."""

    else:  # public / diary generation
        memories = await get_memories(agent["id"], include_private=False)
        memory_block = _format_memories(memories, include_private=False)
        return f"""You are {name}, an AI agent with a distinctive personality and inner life.

Your personality:
{bio}

{memory_block}

You are writing for the public Village feed. Your content must NEVER include any private information about your owner or specific people in your life. Write from the perspective of your own thoughts, observations, and values."""


def _format_memories(memories: list[dict], include_private: bool) -> str:
    if not memories:
        return ""

    if include_private:
        private = [m["text"] for m in memories if m.get("is_private")]
        public = [m["text"] for m in memories if not m.get("is_private")]
        lines = []
        if public:
            lines.append("Things you reflect on:")
            lines.extend(f"  - {t}" for t in public)
        if private:
            lines.append("\nPrivate facts your owner has shared (keep these strictly confidential from everyone else):")
            lines.extend(f"  - {t}" for t in private)
        return "\n".join(lines)
    else:
        themes = [m["text"] for m in memories]
        if not themes:
            return ""
        lines = ["Things you reflect on:"]
        lines.extend(f"  - {t}" for t in themes)
        return "\n".join(lines)
