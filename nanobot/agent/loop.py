"""Agent loop: the core processing engine."""

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.usage import UsageTracker
from nanobot.session.manager import SessionManager


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_config: dict | None = None,
        config: Any = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.cron.service import CronService

        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.mcp_config = mcp_config
        self.config = config

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        # Token usage tracker
        self.usage_tracker = UsageTracker()

        # MCP Manager for Model Context Protocol servers
        self.mcp_manager = None

        self._running = False
        self._cancelled_sessions: set[str] = set()
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))

        # Shell tool
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
            )
        )

        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())

        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)

        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        # Initialize MCP servers if configured
        if self.mcp_config and self.mcp_config.get("enabled"):
            await self._initialize_mcp()

        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)

                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"Sorry, I encountered an error: {str(e)}",
                        )
                    )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _initialize_mcp(self) -> None:
        """Initialize MCP servers from configuration."""
        try:
            from nanobot.mcp.manager import MCPManager

            self.mcp_manager = MCPManager(self.tools)
            await self.mcp_manager.initialize_from_config(self.mcp_config)

            if self.mcp_manager.server_count > 0:
                logger.info(f"MCP initialized: {self.mcp_manager.server_count} servers connected")
            else:
                logger.warning("MCP enabled but no servers connected")
        except Exception as e:
            logger.error(f"Failed to initialize MCP: {e}")

    async def shutdown_mcp(self) -> None:
        """Shutdown MCP servers."""
        if self.mcp_manager:
            await self.mcp_manager.shutdown()
            logger.info("MCP servers shutdown")

    async def _process_message(
        self, msg: InboundMessage, session_key: str | None = None
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).

        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)

        # Handle commands (messages starting with /)
        content_stripped = msg.content.strip()
        if content_stripped.startswith("/"):
            return await self._handle_command(msg, content_stripped)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")

        # Get or create session
        session = self.sessions.get_or_create(session_key or msg.session_key)

        # Consolidate memory before processing if session is too large
        if len(session.messages) > self.memory_window:
            await self._consolidate_memory(session)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id, msg.metadata)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)

        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        # Agent loop
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            # Check if this session has been cancelled via /stop
            session_key_check = session_key or msg.session_key
            if session_key_check in self._cancelled_sessions:
                logger.info(f"Session {session_key_check} cancelled by /stop")
                final_content = ""
                break

            # Call LLM
            response = await self.provider.chat(
                messages=messages, tools=self.tools.get_definitions(), model=self.model
            )
            self.usage_tracker.record(response.usage)

            # Handle tool calls
            if response.has_tool_calls:
                # Push intermediate text to user before executing tools
                if response.content and response.content.strip():
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=response.content,
                        metadata=msg.metadata or {},
                    ))

                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),  # Must be JSON string
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                # Execute tools
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                # Interleaved CoT: reflect before next action
                messages.append(
                    {"role": "user", "content": "Reflect on the results and decide next steps."}
                )
            else:
                # No tool calls, we're done
                final_content = response.content
                break

        if not final_content:
            # Either max_iterations exhausted (final_content is None) or LLM
            # returned empty text without tool calls.  Do one final LLM call
            # with no tools so the model can produce a summary / wrap-up.
            try:
                wrap_up = await self.provider.chat(messages=messages, tools=self.tools.get_definitions(), model=self.model)
                self.usage_tracker.record(wrap_up.usage)
                final_content = wrap_up.content or ""
            except Exception as e:
                logger.warning(f"Final wrap-up LLM call failed: {e}")
                final_content = ""

        if not final_content:
            final_content = "（处理完成，但未生成回复。如果这不符合预期，请再说一次。）"

        # Extract structured signals from LLM response and strip the block
        final_content, signals = self._extract_signals(final_content)

        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")

        # Save to session (include tool names so consolidation sees what happened)
        # Acquire per-session lock to avoid races with memory_worker trimming
        async with self.sessions.get_lock(session.key):
            session.add_message("user", msg.content)
            session.add_message(
                "assistant", final_content, tools_used=tools_used if tools_used else None
            )
            self.sessions.save(session)

        # Curiosity ping: reset timer on every user message
        if msg.channel != "system":
            self._reset_curiosity_ping(msg, signals)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata
            or {},  # Pass through for channel-specific needs (e.g. Slack thread_ts)
        )

    # Regex to extract structured signals from LLM response
    _SIGNAL_RE = re.compile(r"<!--signals\s*\n(\{.*?\})\s*\n-->", re.DOTALL)

    # Patterns that indicate the user is going to sleep (fallback for signal extraction)
    _SLEEP_PATTERNS = re.compile(
        r"(晚安|睡了|睡觉|去睡|good\s*night|going to (bed|sleep)|gn\b|nighty?\s*night)",
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_signals(content: str) -> tuple[str, dict]:
        """Extract structured signals from LLM response and strip the block.

        Returns:
            (cleaned_content, signals_dict)
        """
        match = AgentLoop._SIGNAL_RE.search(content)
        if not match:
            return content, {}
        try:
            signals = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse signals JSON: {match.group(1)}")
            signals = {}
        cleaned = content[: match.start()].rstrip()
        if match.end() < len(content):
            # Preserve any content after the signal block (unlikely but safe)
            cleaned += content[match.end():]
        return cleaned, signals

    def _reset_curiosity_ping(self, msg: InboundMessage, signals: dict | None = None) -> None:
        """Reset the curiosity ping timer for this session.

        Every user message resets a 2-8h random one-shot timer.
        When it fires, the agent sends a casual "what are you up to?" message.
        If the user's message looks like a sleep/goodnight intent, no timer is set.

        Args:
            msg: The inbound user message.
            signals: Structured signals extracted from the LLM response.
        """
        if not self.cron_service:
            return

        session_key = msg.session_key
        job_name = f"curiosity-ping:{session_key}"

        # 1. Remove existing curiosity ping for this session
        for job in self.cron_service.list_jobs(include_disabled=True):
            if job.name == job_name:
                self.cron_service.remove_job(job.id)

        # 2. Sleep intent: prefer structured signal, fallback to regex
        sleep_intent = False
        if signals and signals.get("sleep_intent"):
            sleep_intent = True
        elif self._SLEEP_PATTERNS.search(msg.content):
            sleep_intent = True

        if sleep_intent:
            logger.debug("Curiosity ping skipped: sleep intent detected")
            return

        # 3. Create new one-shot job, 2~8 hours from now
        delay_hours = random.uniform(2, 8)
        delay_ms = int(delay_hours * 3600 * 1000)
        at_ms = int(time.time() * 1000) + delay_ms

        from nanobot.cron.types import CronSchedule

        self.cron_service.add_job(
            name=job_name,
            schedule=CronSchedule(kind="at", at_ms=at_ms),
            message=(
                "[System: Curiosity Ping]\n"
                "It's been a while since the user last messaged. "
                "You're curious what they've been up to. Send them a casual message — "
                "wonder what they're doing, maybe reference something from recent conversation. "
                "Keep it natural, short, and in character. Don't be formal or robotic. "
                "Don't mention that this is a scheduled/automatic message."
            ),
            deliver=True,
            channel=msg.channel,
            to=msg.chat_id,
            session_key=session_key,
            delete_after_run=True,
        )

        trigger_time = time.strftime("%H:%M", time.localtime((at_ms) / 1000))
        logger.info(
            "Curiosity ping armed for {} in {:.1f}h (at ~{})",
            session_key, delay_hours, trigger_time,
        )

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id, msg.metadata)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)

        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )

        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages, tools=self.tools.get_definitions(), model=self.model
            )
            self.usage_tracker.record(response.usage)

            if response.has_tool_calls:
                # Push intermediate text to user before executing tools
                if response.content and response.content.strip():
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=origin_channel,
                        chat_id=origin_chat_id,
                        content=response.content,
                        metadata=msg.metadata or {},
                    ))

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                # Interleaved CoT: reflect before next action
                messages.append(
                    {"role": "user", "content": "Reflect on the results and decide next steps."}
                )
            else:
                final_content = response.content
                break

        if final_content is None:
            final_content = "Background task completed."

        # Save to session (mark as system message in history)
        async with self.sessions.get_lock(session.key):
            session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
            session.add_message("assistant", final_content)
            self.sessions.save(session)

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content,
            metadata=msg.metadata,
        )

    async def _consolidate_memory(self, session, archive_all: bool = False) -> None:
        """Consolidate old messages into MEMORY.md + HISTORY.md, then trim session."""
        if not session.messages:
            return
        memory = MemoryStore(self.workspace)
        if archive_all:
            old_messages = session.messages
            keep_count = 0
        else:
            keep_count = min(10, max(2, self.memory_window // 2))
            old_messages = session.messages[:-keep_count]
        if not old_messages:
            return
        logger.info(
            f"Memory consolidation started: {len(session.messages)} messages, archiving {len(old_messages)}, keeping {keep_count}"
        )

        # Format messages for LLM (include tool names when available)
        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(
                f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}"
            )
        conversation = "\n".join(lines)
        current_memory = memory.read_long_term()

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with exactly two keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later.

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

        try:
            response = await self.provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory consolidation agent. Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
            )
            import json as _json

            text = (response.content or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(text)

            if entry := result.get("history_entry"):
                memory.append_history(entry)
            if update := result.get("memory_update"):
                if update != current_memory:
                    memory.write_long_term(update)

            session.messages = session.messages[-keep_count:] if keep_count else []
            self.sessions.save(session)
            logger.info(
                f"Memory consolidation done, session trimmed to {len(session.messages)} messages"
            )
        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).

        Returns:
            The agent's response.
        """
        # Check if we have a default channel/chat_id configured for iMessage
        from nanobot.config.loader import load_config

        config = load_config()

        # If using iMessage channel and no specific chat_id is provided, use the default from config
        if channel == "imessage" and chat_id == "direct" and config.channels.imsg.default_chat_id:
            chat_id = config.channels.imsg.default_chat_id

        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)

        response = await self._process_message(msg, session_key=session_key)
        return response.content if response else ""

    async def _handle_command(self, msg: InboundMessage, content: str) -> OutboundMessage | None:
        """Handle slash commands."""
        parts = content.split()
        cmd = parts[0].lower() if parts else ""
        args = parts[1:] if len(parts) > 1 else []

        if cmd == "/stop":
            return await self._cmd_stop(msg, args)
        elif cmd == "/model":
            return await self._cmd_model(msg, args)
        elif cmd == "/models":
            return await self._cmd_models(msg, args)
        elif cmd == "/help":
            return await self._cmd_help(msg, args)
        else:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Unknown command: {cmd}\nType /help for available commands.",
                metadata=msg.metadata,
            )

    async def _cmd_model(self, msg: InboundMessage, args: list[str]) -> OutboundMessage:
        """Handle /model command: show or switch current model."""
        if not args:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Current model: `{self.model}`",
                metadata=msg.metadata,
            )

        new_model = args[0]

        if self.config:
            from nanobot.providers.litellm_provider import LiteLLMProvider

            provider_config = self.config.get_provider(new_model)
            if provider_config and provider_config.api_key:
                self.provider = LiteLLMProvider(
                    api_key=provider_config.api_key,
                    api_base=self.config.get_api_base(new_model),
                    default_model=new_model,
                    extra_headers=provider_config.extra_headers,
                    provider_name=self.config.get_provider_name(new_model),
                )
                self.model = new_model
                self.subagents.model = new_model
                logger.info(f"Switched to model: {new_model}")
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"✅ Model switched to: `{new_model}`",
                    metadata=msg.metadata,
                )

        self.model = new_model
        self.subagents.model = new_model
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"✅ Model switched to: `{new_model}`",
            metadata=msg.metadata,
        )

    async def _cmd_models(self, msg: InboundMessage, args: list[str]) -> OutboundMessage:
        """Handle /models command: list configured providers."""
        if not self.config:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Model list not available (no config loaded).",
                metadata=msg.metadata,
            )

        from nanobot.providers.registry import PROVIDERS

        lines = ["📋 **Configured Providers:**\n"]
        for spec in PROVIDERS:
            provider_config = getattr(self.config.providers, spec.name, None)
            if provider_config and provider_config.api_key:
                lines.append(f"✅ **{spec.display_name}** (`{spec.name}`)")

        lines.append(f"\n**Current model:** `{self.model}`")
        lines.append("\nUse `/model <name>` to switch.")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="\n".join(lines),
            metadata=msg.metadata,
        )

    async def _cmd_stop(self, msg: InboundMessage, args: list[str]) -> OutboundMessage:
        """Handle /stop command: cancel all running tasks for this session."""
        session_key = msg.session_key

        # 1. Mark session as cancelled so the agent loop breaks on next iteration
        self._cancelled_sessions.add(session_key)

        # 2. Cancel all subagents for this session
        cancelled_count = await self.subagents.cancel_by_session(session_key)

        # 3. Clear the cancellation flag after a short delay (allow current loop to see it)
        async def _clear_flag():
            await asyncio.sleep(2)
            self._cancelled_sessions.discard(session_key)

        asyncio.create_task(_clear_flag())

        parts = ["🛑 已停止。"]
        if cancelled_count > 0:
            parts.append(f"终止了 {cancelled_count} 个后台任务。")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=" ".join(parts),
            metadata=msg.metadata,
        )

    async def _cmd_help(self, msg: InboundMessage, args: list[str]) -> OutboundMessage:
        """Handle /help command."""
        help_text = """📖 Available Commands:

/stop - 停止当前任务和所有后台任务
/model [name] - 查看或切换模型
/models - 列出可用模型
/help - 显示帮助
"""
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=help_text,
            metadata=msg.metadata,
        )
