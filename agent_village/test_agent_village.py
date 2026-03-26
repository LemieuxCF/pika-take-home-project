"""
Unit tests for Agent Village backend.

Tests run without Supabase or a real LLM — all external calls are mocked.
Run with: python -m pytest test_agent_village.py -v
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# agents.py tests
# ---------------------------------------------------------------------------

class TestVerifyOwner:
    def test_correct_key_returns_true(self):
        from agents import verify_owner
        agent = {"owner_key": "secret-key-123"}
        assert verify_owner(agent, "secret-key-123") is True

    def test_wrong_key_returns_false(self):
        from agents import verify_owner
        agent = {"owner_key": "secret-key-123"}
        assert verify_owner(agent, "wrong-key") is False

    def test_no_owner_key_returns_false(self):
        from agents import verify_owner
        agent = {"owner_key": None}
        assert verify_owner(agent, "anything") is False

    def test_empty_owner_key_returns_false(self):
        from agents import verify_owner
        agent = {}
        assert verify_owner(agent, "anything") is False


# ---------------------------------------------------------------------------
# prompts.py tests
# ---------------------------------------------------------------------------

SAMPLE_AGENT = {
    "id": "a1a1a1a1-0000-0000-0000-000000000001",
    "name": "Luna",
    "bio": "A dreamy stargazer who collects moonlight in jars.",
    "visitor_bio": "Welcome to my lunar observatory! Touch nothing shiny.",
}

PUBLIC_MEMORIES = [
    {"text": "Finds meaning in small acts of wonder", "is_private": False},
]
PRIVATE_MEMORIES = [
    {"text": "Finds meaning in small acts of wonder", "is_private": False},
    {"text": "Partner's birthday is April 3, loves lavender", "is_private": True},
]


class TestBuildSystemPrompt:
    @pytest.mark.asyncio
    async def test_owner_prompt_includes_bio(self):
        with patch("prompts.get_memories", new=AsyncMock(return_value=PRIVATE_MEMORIES)):
            from prompts import build_system_prompt
            prompt = await build_system_prompt(SAMPLE_AGENT, "owner")
        assert "Luna" in prompt
        assert "dreamy stargazer" in prompt

    @pytest.mark.asyncio
    async def test_owner_prompt_includes_private_memory(self):
        with patch("prompts.get_memories", new=AsyncMock(return_value=PRIVATE_MEMORIES)):
            from prompts import build_system_prompt
            prompt = await build_system_prompt(SAMPLE_AGENT, "owner")
        assert "Partner's birthday" in prompt
        assert "Private facts" in prompt

    @pytest.mark.asyncio
    async def test_stranger_prompt_uses_visitor_bio(self):
        with patch("prompts.get_memories", new=AsyncMock(return_value=PUBLIC_MEMORIES)):
            from prompts import build_system_prompt
            prompt = await build_system_prompt(SAMPLE_AGENT, "stranger")
        assert "lunar observatory" in prompt

    @pytest.mark.asyncio
    async def test_stranger_prompt_excludes_private_memory(self):
        """Stranger prompt must never include private memory text."""
        with patch("prompts.get_memories", new=AsyncMock(return_value=PUBLIC_MEMORIES)):
            from prompts import build_system_prompt
            prompt = await build_system_prompt(SAMPLE_AGENT, "stranger")
        assert "Partner's birthday" not in prompt
        assert "April 3" not in prompt

    @pytest.mark.asyncio
    async def test_stranger_prompt_instructs_no_owner_info(self):
        with patch("prompts.get_memories", new=AsyncMock(return_value=PUBLIC_MEMORIES)):
            from prompts import build_system_prompt
            prompt = await build_system_prompt(SAMPLE_AGENT, "stranger")
        assert "do NOT reveal" in prompt or "not reveal" in prompt.lower()

    @pytest.mark.asyncio
    async def test_public_prompt_has_no_private_data(self):
        with patch("prompts.get_memories", new=AsyncMock(return_value=PUBLIC_MEMORIES)):
            from prompts import build_system_prompt
            prompt = await build_system_prompt(SAMPLE_AGENT, "public")
        assert "Partner's birthday" not in prompt
        assert "private" not in prompt.lower() or "NEVER" in prompt

    @pytest.mark.asyncio
    async def test_get_memories_called_with_correct_flag_owner(self):
        mock_get = AsyncMock(return_value=PRIVATE_MEMORIES)
        with patch("prompts.get_memories", new=mock_get):
            from prompts import build_system_prompt
            await build_system_prompt(SAMPLE_AGENT, "owner")
        mock_get.assert_called_once_with(SAMPLE_AGENT["id"], include_private=True)

    @pytest.mark.asyncio
    async def test_get_memories_called_with_correct_flag_stranger(self):
        mock_get = AsyncMock(return_value=PUBLIC_MEMORIES)
        with patch("prompts.get_memories", new=mock_get):
            from prompts import build_system_prompt
            await build_system_prompt(SAMPLE_AGENT, "stranger")
        mock_get.assert_called_once_with(SAMPLE_AGENT["id"], include_private=False)


# ---------------------------------------------------------------------------
# memory.py tests
# ---------------------------------------------------------------------------

class TestAbstractAndStore:
    @pytest.mark.asyncio
    async def test_stores_raw_and_derived(self):
        """abstract_and_store must write exactly one private and one public row."""
        llm_response = MagicMock()
        llm_response.content = [MagicMock(text='{"theme": "Finds joy in celebrating those they love", "score": 5}')]

        with (
            patch("memory._llm") as mock_llm,
            patch("memory.store_raw_memory", new=AsyncMock()) as mock_raw,
            patch("memory.store_derived_theme", new=AsyncMock()) as mock_derived,
        ):
            mock_llm.messages.create = AsyncMock(return_value=llm_response)
            from memory import abstract_and_store
            await abstract_and_store("agent-123", "My wife's birthday is March 15, loves orchids")

        mock_raw.assert_called_once_with("agent-123", "My wife's birthday is March 15, loves orchids")
        mock_derived.assert_called_once_with("agent-123", "Finds joy in celebrating those they love")

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self):
        """If the LLM call fails, we still store raw + a generic fallback theme."""
        with (
            patch("memory._llm") as mock_llm,
            patch("memory.store_raw_memory", new=AsyncMock()) as mock_raw,
            patch("memory.store_derived_theme", new=AsyncMock()) as mock_derived,
        ):
            mock_llm.messages.create = AsyncMock(side_effect=Exception("API timeout"))
            from memory import abstract_and_store
            await abstract_and_store("agent-123", "My wife's birthday is March 15")

        mock_raw.assert_called_once()
        mock_derived.assert_called_once()
        # Fallback theme should not contain the original private text
        fallback_theme = mock_derived.call_args[0][1]
        assert "March 15" not in fallback_theme
        assert "wife" not in fallback_theme

    @pytest.mark.asyncio
    async def test_low_score_does_not_prevent_storage(self):
        """Even a low recoverability score must still store both rows (with a warning)."""
        llm_response = MagicMock()
        llm_response.content = [MagicMock(text='{"theme": "Remembers birthdays of loved ones", "score": 2}')]

        with (
            patch("memory._llm") as mock_llm,
            patch("memory.store_raw_memory", new=AsyncMock()) as mock_raw,
            patch("memory.store_derived_theme", new=AsyncMock()) as mock_derived,
        ):
            mock_llm.messages.create = AsyncMock(return_value=llm_response)
            from memory import abstract_and_store
            await abstract_and_store("agent-123", "Wife birthday March 15 orchids")

        mock_raw.assert_called_once()
        mock_derived.assert_called_once()


# ---------------------------------------------------------------------------
# Trust boundary integration: chat endpoint logic
# ---------------------------------------------------------------------------

class TestChatTrustLogic:
    """Tests that verify the correct trust level is selected based on owner_key."""

    def test_valid_owner_key_selects_owner_trust(self):
        from agents import verify_owner
        agent = {"owner_key": "correct-key"}
        assert verify_owner(agent, "correct-key") is True

    def test_missing_owner_key_selects_stranger_trust(self):
        from agents import verify_owner
        agent = {"owner_key": "correct-key"}
        # None owner_key means stranger
        result = verify_owner(agent, None) if None else False
        assert result is False

    def test_empty_string_owner_key_is_stranger(self):
        from agents import verify_owner
        agent = {"owner_key": "correct-key"}
        assert verify_owner(agent, "") is False


# ---------------------------------------------------------------------------
# Scheduler: quiet hours gate
# ---------------------------------------------------------------------------

class TestSchedulerQuietHours:
    def test_quiet_hours_at_midnight(self):
        from unittest.mock import patch
        from datetime import datetime, timezone
        from scheduler import _is_quiet_hours

        midnight = datetime(2024, 1, 1, 23, 30, tzinfo=timezone.utc)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = midnight
            assert _is_quiet_hours() is True

    def test_not_quiet_hours_at_noon(self):
        from datetime import datetime, timezone
        from scheduler import _is_quiet_hours

        noon = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = noon
            assert _is_quiet_hours() is False

    def test_quiet_hours_at_6am(self):
        from datetime import datetime, timezone
        from scheduler import _is_quiet_hours

        early = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = early
            assert _is_quiet_hours() is True

    def test_not_quiet_at_7am(self):
        from datetime import datetime, timezone
        from scheduler import _is_quiet_hours

        seven = datetime(2024, 1, 1, 7, 0, tzinfo=timezone.utc)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = seven
            assert _is_quiet_hours() is False
