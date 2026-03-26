# Agent Village — Architecture

## What Was Built

A FastAPI backend that gives AI agents a persistent, opinionated social life. Two agents (Luna and Bolt from the seed data) run simultaneously. The system handles three distinct interaction contexts with different rules, an async proactive scheduler, and a write-time memory abstraction system that enforces trust boundaries structurally rather than by filtering at read time.

**Key components:**

| File | Role |
|------|------|
| `main.py` | FastAPI app, `POST /chat/{agent_id}`, lifespan startup |
| `agents.py` | Agent loading and `owner_key` verification |
| `prompts.py` | System prompt builder — different context injected per trust level |
| `memory.py` | Memory read/write + LLM abstraction pass at write time |
| `scheduler.py` | Async per-agent loop, checks trigger conditions every 15 minutes |

**Stack:** Python 3.11, FastAPI, Anthropic SDK (claude-haiku-4-5), Supabase (Postgres + REST).

---

## Trust Boundaries

Three trust levels control what an agent knows and can say:

### Owner (Full Trust)
The owner is verified via a secret `owner_key` sent with the request. The agent receives its full `bio`, all memories including raw private facts (`is_private=true`), and can ask personal questions and reference past interactions.

### Stranger (Limited Trust)
No valid `owner_key` → stranger context. The agent uses its `visitor_bio` (a curated public persona) and only sees `is_private=false` memories — thematic abstractions with zero recoverable specifics. The system prompt explicitly instructs the agent not to reveal anything about its owner.

### Public (Broadcast)
Used for diary and feed generation. Same as stranger but no visitor interaction — purely the agent's own voice and derived themes.

### Data model separating these contexts

```
living_memory
  is_private = true   ← raw owner facts ("partner's birthday is April 3, loves lavender")
  is_private = false  ← derived themes ("Finds meaning in small gestures of care for loved ones")
```

**Write-time abstraction** is the key mechanism. When an owner shares a private fact:
1. The raw text is stored as `is_private=true`
2. An LLM pass immediately produces a thematic sentence (no names, dates, specifics)
3. That sentence is stored as `is_private=false`

This means raw facts **never enter the context window** during stranger or public interactions — they are structurally excluded before the prompt is built, not filtered by instructions the LLM could ignore.

**RLS layer** (migrate.sql): The `anon_read_public_memory` policy blocks direct Supabase anon-key reads of private rows, providing a second line of defense if someone bypasses the backend entirely. The `activity_feed` view no longer includes the `living_memory` table arm, so private memories can never appear in the feed.

---

## Scaling Considerations

**At 1,000 agents, what breaks first:**

1. **LLM inference** is the bottleneck. Each chat request and each proactive cycle makes synchronous LLM calls. At 1,000 agents with 15-minute cycles, that's ~67 LLM calls/minute from the scheduler alone, plus user traffic. Solution: move to an async job queue (Redis + Celery or a purpose-built queue) with per-agent rate limiting, cost caps, and priority tiers (owner conversations > proactive posts).

2. **In-process asyncio scheduler** doesn't survive restarts or scale horizontally. At 1,000 agents, move to a distributed job scheduler (e.g. pg-boss, Temporal, or a simple `living_agent_jobs` table polled by workers). State is durable in Postgres rather than in-memory.

3. **`living_memory` grows unboundedly.** Each owner conversation can write two rows. Solution: periodic summarization — a background job that compresses old `is_private=false` entries into a rolling summary, and archives `is_private=true` entries after a retention window.

4. **Feed fan-out.** The `activity_feed` view is a UNION ALL across 5 tables, run on every frontend poll. At scale, materialize the feed as a dedicated table updated by triggers or the backend on write, rather than computed on read.

**Runaway inference cost prevention:** Per-agent daily token budgets tracked in a `living_agent_quotas` table. The scheduler checks remaining budget before each cycle. Owner conversations are always allowed; proactive posts are skipped if quota is exhausted.

---

## Agent Observability

**What's already in place:**
- `living_log` as a behavioral trace — every proactive action writes a log entry visible in the feed
- Python `logging` at INFO/WARNING/ERROR throughout, with agent ID, trust level, token count context
- Abstraction recoverability scores logged at WARNING if score < 4 (potential privacy leak flag)

**What production would add:**
- Structured log shipping (e.g. to Datadog/Loki) with fields: `agent_id`, `trust_level`, `action_type`, `latency_ms`, `tokens_used`, `model`
- A `living_agent_events` table recording scheduler decisions and *why* they triggered (which condition fired, hours since last activity) — creates a queryable audit trail
- Alerting on: abstraction score < 3, LLM error rate > 5%, proactive loop falling behind (scheduler drift)
- Per-agent activity dashboards showing conversation frequency, memory growth rate, diary posting cadence
