"""Background worker for periodic session memory archiving."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager
from nanobot.utils.helpers import ensure_dir, safe_filename


class MemoryWorker:
    """Background memory worker that archives old session messages."""

    def __init__(
        self,
        session_manager: SessionManager,
        memory_store: MemoryStore,
        provider: LLMProvider,
        model: str = "anthropic/claude-haiku",
        check_interval: int = 300,
        max_idle_hours: float = 4.0,
        max_messages: int = 20,
        retain_recent: int = 6,
        backup_before_trim: bool = False,
        backup_dir: Path | None = None,
        notify_user: bool = False,
        notification_message: str = "I organized our earlier conversation into memory.",
        notify_callback: Callable[[str, str, str], Awaitable[None]] | None = None,
    ):
        self.session_manager = session_manager
        self.memory_store = memory_store
        self.provider = provider
        self.model = model
        self.check_interval = max(10, check_interval)
        self.max_idle_hours = max_idle_hours
        self.max_messages = max_messages
        self.retain_recent = max(0, retain_recent)
        self.backup_before_trim = backup_before_trim
        self.backup_dir = backup_dir or (memory_store.memory_dir / "session_backups")
        self.notify_user = notify_user
        self.notification_message = notification_message
        self.notify_callback = notify_callback
        self._running = False

    async def run(self) -> None:
        """Start periodic memory processing."""
        self._running = True
        logger.info(
            f"Memory worker started (interval={self.check_interval}s, model={self.model})"
        )
        while self._running:
            try:
                await self._check_all_sessions()
            except Exception as e:
                logger.error(f"Memory worker tick failed: {e}")
            await asyncio.sleep(self.check_interval)

    def stop(self) -> None:
        """Stop periodic worker loop."""
        self._running = False
        logger.info("Memory worker stopping")

    async def _check_all_sessions(self) -> None:
        """Scan sessions and archive sessions meeting trigger conditions."""
        for info in self.session_manager.list_sessions():
            key = info.get("key")
            if not key:
                continue
            session = self.session_manager.get_or_create(key)
            reason = self._should_archive(session)
            if reason:
                await self._archive_session(session, reason)

    def _should_archive(self, session: Session) -> str | None:
        """Return trigger reason when session should be archived."""
        if len(session.messages) <= self.retain_recent:
            return None

        if self.max_messages > 0 and len(session.messages) > self.max_messages:
            return "messages"

        timestamps = [
            ts
            for msg in session.messages
            if (ts := self._parse_timestamp(msg.get("timestamp"))) is not None
        ]

        if not timestamps:
            return None

        now = datetime.now()
        oldest = min(timestamps)
        latest = max(timestamps)

        if self.max_idle_hours > 0 and now - latest > timedelta(hours=self.max_idle_hours):
            return "idle"

        if oldest.date() != latest.date():
            return "day_boundary"

        return None

    async def _archive_session(self, session: Session, reason: str) -> None:
        """Archive old messages and trim session history."""
        lock = self.session_manager.get_lock(session.key)
        async with lock:
            await self._do_archive(session, reason)

    async def _do_archive(self, session: Session, reason: str) -> None:
        """Inner archive logic, called under session lock."""
        old_messages = session.messages[:-self.retain_recent] if self.retain_recent else session.messages
        if not old_messages:
            return

        logger.info(
            f"Memory worker archiving {session.key}: reason={reason}, total={len(session.messages)}, archive={len(old_messages)}, keep={self.retain_recent}"
        )

        if self.backup_before_trim:
            self._backup_session(session)

        conversation = self._format_messages(old_messages)
        current_memory = self.memory_store.read_long_term()

        prompt = f"""You are a memory consolidation agent. Analyze the conversation and produce two sections separated by exact delimiters.

Section 1 — HISTORY: A concise paragraph (2-5 sentences) summarizing key events/decisions.
Start with timestamp prefix [YYYY-MM-DD HH:MM].

Section 2 — MEMORY: Updated long-term memory in markdown format.
Add newly learned durable facts (preferences, profile, project context, decisions).
Keep useful existing facts. If nothing new was learned, return the original content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Archive
{conversation}

Respond in EXACTLY this format (delimiters must appear on their own lines):

===HISTORY===
(your history summary here)
===MEMORY===
(your updated memory markdown here)
===END==="""

        try:
            response = await self.provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a memory consolidation agent. "
                            "Always respond using the ===HISTORY===, ===MEMORY===, ===END=== "
                            "delimiter format as instructed. Never use JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
            )
            result = self._parse_response(response.content or "")

            history_entry = result.get("history_entry", "").strip()
            memory_update = result.get("memory_update", "").strip()

            if history_entry:
                self.memory_store.append_history(history_entry)
            if memory_update and memory_update != current_memory:
                self.memory_store.write_long_term(memory_update)

            session.messages = session.messages[-self.retain_recent:] if self.retain_recent else []
            session.updated_at = datetime.now()
            self.session_manager.save(session)

            if self.notify_user:
                await self._notify_user(session.key)

            logger.info(f"Memory worker archived session {session.key}")
        except Exception as e:
            logger.error(f"Memory worker failed to archive {session.key}: {e}")

    def _backup_session(self, session: Session) -> None:
        """Write full session JSONL snapshot before trimming."""
        backup_dir = ensure_dir(self.backup_dir)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = safe_filename(session.key.replace(":", "_"))
        backup_path = backup_dir / f"{name}_{ts}.jsonl"
        with open(backup_path, "w", encoding="utf-8") as f:
            for message in session.messages:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")

    async def _notify_user(self, session_key: str) -> None:
        """Notify user about memory archiving if callback available."""
        if not self.notify_callback:
            return
        if ":" not in session_key:
            return
        channel, chat_id = session_key.split(":", 1)
        if not channel or not chat_id:
            return
        try:
            await self.notify_callback(channel, chat_id, self.notification_message)
        except Exception as e:
            logger.warning(f"Memory worker notification failed for {session_key}: {e}")

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Format conversation block passed to memory model."""
        lines = []
        for message in messages:
            content = message.get("content")
            if not content:
                continue
            tools = (
                f" [tools: {', '.join(message['tools_used'])}]"
                if message.get("tools_used")
                else ""
            )
            lines.append(
                f"[{(message.get('timestamp') or '?')[:16]}] {message.get('role', 'unknown').upper()}{tools}: {content}"
            )
        return "\n".join(lines)

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse delimiter-based response from the memory LLM.

        Expected format:
            ===HISTORY===
            ...history content...
            ===MEMORY===
            ...memory content...
            ===END===

        Falls back to treating entire content as history if delimiters missing.
        """
        text = content.strip()
        result: dict[str, Any] = {"history_entry": "", "memory_update": ""}

        # Try delimiter-based extraction
        history_match = re.search(
            r"===HISTORY===\s*\n(.*?)(?=\n===MEMORY===)",
            text,
            re.DOTALL,
        )
        memory_match = re.search(
            r"===MEMORY===\s*\n(.*?)(?=\n===END===|$)",
            text,
            re.DOTALL,
        )

        if history_match:
            result["history_entry"] = history_match.group(1).strip()
        if memory_match:
            result["memory_update"] = memory_match.group(1).strip()

        if not history_match and not memory_match:
            # LLM ignored the format — try JSON as legacy fallback
            try:
                parsed = json.loads(text)
                result["history_entry"] = parsed.get("history_entry", "")
                result["memory_update"] = parsed.get("memory_update", "")
                logger.warning("Memory worker fell back to JSON parsing")
            except json.JSONDecodeError:
                logger.error(
                    "Memory worker could not parse LLM response (no delimiters, no JSON), skipping"
                )

        return result

    @staticmethod
    def _parse_timestamp(raw: Any) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
