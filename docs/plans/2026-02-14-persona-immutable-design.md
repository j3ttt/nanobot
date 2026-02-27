# Persona Immutability & Memory Isolation Design

**Date**: 2026-02-14
**Author**: Claude Code
**Status**: Approved

---

## Executive Summary

This design implements a three-layer memory architecture to ensure consistent persona across all chat sessions while preventing persona drift through memory contamination. The solution maintains backward compatibility while adding session-scoped memory isolation.

**Core Goals**:
1. All chat sessions share the same core persona
2. Different chat contexts cannot contaminate the core persona
3. Chat information enters global facts/knowledge layer but cannot modify persona layer
4. Persona drift detection (warn-only, no auto-rewrite)

---

## Problem Analysis

### Current Issues

**1. Memory Contamination**
- All sessions share a single `MEMORY.md` file
- Content from different groups/chats mixes together
- Group A's preferences contaminate Group B's context

**2. Lack of Persona Protection**
- No mechanism prevents automatic processes from modifying `SOUL.md` or `AGENTS.md`
- `_consolidate_memory()` could theoretically write to any file
- Persona definitions can drift over time

**3. Inconsistent Behavior Across Sessions**
- Although `SOUL.md` remains unchanged physically, behavior differs between sessions
- Root cause: contaminated shared memory + session-specific history creates different effective contexts
- Example:
  - Group A: `[SOUL] + [MEMORY: mixed] + [History: 100 coding msgs]` → coding assistant
  - Group B: `[SOUL] + [MEMORY: mixed] + [History: 100 cooking msgs]` → cooking assistant

**4. Injection Order Confusion**
- Bootstrap files load in arbitrary order: `["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]`
- No clear priority hierarchy
- `MEMORY.md` positioned in middle of system prompt (medium weight)

### Why Current Architecture Fails

```
Current State:
┌─ System Prompt ────────────────────┐
│ Core Identity                      │
│ Bootstrap (mixed persona+config)   │
│ MEMORY.md (contaminated)           │  ← Shared across all sessions
│ Skills                             │
└────────────────────────────────────┘
┌─ History ──────────────────────────┐
│ session.get_history()              │  ← Session-specific but polluted
└────────────────────────────────────┘

Problem: Even though SOUL.md is same, different histories + contaminated
shared memory = inconsistent behavior
```

---

## Solution: Three-Layer Memory Architecture

### File Structure

```
workspace/
├── SOUL.md                          # Persona layer (read-only)
├── AGENTS.md                        # Persona layer (read-only)
├── USER.md, TOOLS.md, etc.         # Other configs
└── memory/
    ├── MEMORY_GLOBAL.md             # Global facts layer (NEW)
    ├── MEMORY.md                    # Deprecated (kept for compatibility)
    ├── HISTORY.md                   # History log (unchanged)
    └── scoped/
        ├── telegram_123456.md       # Session-scoped memory
        ├── whatsapp_1234567890.md
        └── feishu_ou_abc123.md
```

### Three Memory Layers

#### 1. Persona Layer (Read-Only)
- **Files**: `SOUL.md`, `AGENTS.md`
- **Characteristics**: Immutable by automatic processes, manual edit only
- **Purpose**: Define core personality, values, behavior rules
- **Protection**: Any automatic write attempt is rejected with error log

#### 2. Global Facts Layer (Shared)
- **File**: `MEMORY_GLOBAL.md`
- **Characteristics**: Shared across all sessions
- **Purpose**: Store long-term stable facts (user preferences, location, habits, project context)
- **Updates**: Automatically updated by consolidation

#### 3. Session-Scoped Memory Layer (Isolated)
- **Files**: `memory/scoped/<safe_session_key>.md`
- **Characteristics**: Session-isolated (each group/chat independent)
- **Purpose**: Store temporary session context (todos, discussion topics)
- **Updates**: Automatically updated by consolidation

### Session Key Conversion

**Format**: `channel:chat_id` → safe filename

**Conversion Rules**:
```python
"telegram:123456"         → "telegram_123456.md"
"whatsapp:+1234567890"    → "whatsapp_1234567890.md"  # Remove +
"feishu:ou_abc-123"       → "feishu_ou_abc_123.md"    # - → _
"discord:user#1234"       → "discord_user_1234.md"    # # → _
```

Algorithm:
1. Replace `:` with `_`
2. Remove or replace special chars (`+`, `#`, `@`, `/`, `\`, spaces → `_`)
3. Keep only letters, numbers, `_`, `-`
4. Strip leading/trailing `_`

---

## Architecture Design

### Component 1: Configuration Schema

**New Config Fields** (`nanobot/config/schema.py`):

```python
class MemoryConfig(BaseModel):
    """Memory system configuration."""
    enabled: bool = True
    persona_immutable: bool = True      # NEW: Persona write protection
    scoped_enabled: bool = True          # NEW: Enable session-scoped memory

class GuardrailsConfig(BaseModel):      # NEW: Guardrails config
    """Guardrails configuration."""
    persona_drift_mode: str = "warn"     # "off" | "warn"
```

**Config Example**:
```json
{
  "memory": {
    "enabled": true,
    "persona_immutable": true,
    "scoped_enabled": true
  },
  "guardrails": {
    "persona_drift_mode": "warn"
  }
}
```

**Compatibility Strategy**:
- All new fields have defaults → old configs work without changes
- `persona_immutable` defaults to `true` (safe by default)
- If `scoped_enabled=false`, fallback to global memory mode (all sessions share `MEMORY_GLOBAL.md`)

### Component 2: Memory Store Extension

**Extended MemoryStore** (`nanobot/agent/memory.py`):

```python
class MemoryStore:
    """Three-layer memory: Persona (read-only) + Global + Scoped."""

    def __init__(self, workspace: Path, config: MemoryConfig):
        self.workspace = workspace
        self.config = config
        self.memory_dir = ensure_dir(workspace / "memory")
        self.scoped_dir = ensure_dir(self.memory_dir / "scoped")

        # Persona files (read-only)
        self.persona_files = {
            workspace / "SOUL.md",
            workspace / "AGENTS.md"
        }

        # Global memory
        self.global_file = self.memory_dir / "MEMORY_GLOBAL.md"

        # Legacy compatibility
        self.legacy_memory_file = self.memory_dir / "MEMORY.md"
```

**New Core Methods**:

```python
# Global memory
def read_global() -> str
def write_global(content: str) -> None

# Session-scoped memory
def read_scoped(session_key: str) -> str
def write_scoped(session_key: str, content: str) -> None

# Persona protection
def is_persona_file(path: Path) -> bool
def check_persona_write_guard(path: Path) -> None  # Raises if persona file

# Utilities
def _safe_session_key(session_key: str) -> str  # "telegram:123" → "telegram_123.md"
```

**Persona Protection Logic**:

```python
def check_persona_write_guard(self, path: Path) -> None:
    """Raise exception if attempting to write to persona file."""
    if not self.config.persona_immutable:
        return  # Protection disabled

    if self.is_persona_file(path):
        logger.error(f"❌ Blocked write to persona file: {path}")
        raise PermissionError(
            f"Cannot modify persona file '{path.name}'. "
            f"Set memory.persona_immutable=false to allow."
        )
```

**Migration Strategy**:

```python
def _migrate_legacy_memory(self) -> None:
    """Migrate MEMORY.md → MEMORY_GLOBAL.md on first startup."""
    if self.legacy_memory_file.exists() and not self.global_file.exists():
        logger.info("Migrating MEMORY.md → MEMORY_GLOBAL.md")
        content = self.legacy_memory_file.read_text()
        self.write_global(content)
```

### Component 3: Context Builder Enhancement

**Modified ContextBuilder** (`nanobot/agent/context.py`):

**New Injection Order**:

```
System Prompt (enforced order):
1. SOUL.md + AGENTS.md         ← Persona layer (highest priority)
2. MEMORY_GLOBAL.md            ← Global facts
3. scoped/<session_key>.md     ← Session context (near history)
4. Skills                      ← Tool definitions
5. Session Info                ← channel + chat_id

Message History:
6. session.get_history()       ← Recent conversation

Current Turn:
7. User's current message
```

**Updated API**:

```python
def build_system_prompt(self, session_key: str | None = None) -> str:
    """Build system prompt with session-aware memory injection."""
    parts = []

    # 1. Persona layer (read-only files)
    parts.append(self._load_persona_files())  # SOUL.md + AGENTS.md

    # 2. Global facts layer
    global_memory = self.memory.read_global()
    if global_memory:
        parts.append(f"# Global Memory\n\n{global_memory}")

    # 3. Session-scoped layer
    if session_key:
        scoped_memory = self.memory.read_scoped(session_key)
        if scoped_memory:
            parts.append(f"# Session Context\n\n{scoped_memory}")

    # 4. Skills (unchanged)
    # ... existing skills loading logic ...

    return "\n\n---\n\n".join(parts)

def _load_persona_files(self) -> str:
    """Load persona files in fixed order."""
    parts = []
    for filename in ["SOUL.md", "AGENTS.md"]:
        file_path = self.workspace / filename
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            parts.append(f"## {filename}\n\n{content}")
    return "\n\n".join(parts) if parts else ""
```

**Updated `build_messages()`**:

```python
def build_messages(
    self,
    history: list[dict[str, Any]],
    current_message: str,
    session_key: str | None = None,  # NEW parameter
    skill_names: list[str] | None = None,
    media: list[str] | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build complete message list with session-aware context."""
    messages = []

    # System prompt with session key
    system_prompt = self.build_system_prompt(
        session_key=session_key,  # Pass session key
        skill_names=skill_names
    )
    messages.append({"role": "system", "content": system_prompt})

    # History + current message (unchanged)
    messages.extend(history)
    user_content = self._build_user_content(current_message, media)
    messages.append({"role": "user", "content": user_content})

    return messages
```

### Component 4: Consolidation Refactor

**Modified `_consolidate_memory()`** (`nanobot/agent/loop.py`):

**Key Changes**:
1. LLM prompt explicitly splits outputs into `global_update` and `scoped_update`
2. Write to different files based on update type
3. **Never** write to persona files

**New Implementation**:

```python
async def _consolidate_memory(self, session, session_key: str) -> None:
    """Consolidate memory with persona protection and scoped isolation."""
    memory = MemoryStore(self.workspace, self.config.memory)
    keep_count = min(10, max(2, self.memory_window // 2))
    old_messages = session.messages[:-keep_count]
    if not old_messages:
        return

    logger.info(f"Consolidating {len(old_messages)} messages for session {session_key}")

    # Format conversation
    conversation = self._format_messages_for_consolidation(old_messages)
    current_global = memory.read_global()
    current_scoped = memory.read_scoped(session_key)

    # NEW: Split prompt with persona protection
    prompt = f"""You are a memory consolidation agent. Return a JSON object with two keys:

1. "global_update": Update GLOBAL FACTS only (user location, real-world preferences,
   technical decisions, long-term context). Remove session-specific items.

2. "scoped_update": Update SESSION CONTEXT only (current todos, discussion topics,
   temporary preferences). Session-specific information goes here.

⚠️ CRITICAL: Do NOT modify persona files (SOUL.md, AGENTS.md).
Only update factual memory, not personality, values, or behavior rules.

## Current Global Memory
{current_global or "(empty)"}

## Current Session Memory ({session_key})
{current_scoped or "(empty)"}

## Conversation to Process
{conversation}

Return JSON: {{"global_update": "...", "scoped_update": "..."}}
"""

    # Call LLM
    response = await self._call_consolidation_llm(prompt)
    result = json.loads(response)

    # Write to separate layers (persona files are protected)
    if result.get("global_update"):
        memory.write_global(result["global_update"])

    if result.get("scoped_update"):
        memory.write_scoped(session_key, result["scoped_update"])

    # Append to history log (unchanged)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    history_entry = f"[{timestamp}] Session: {session_key}\n{result.get('scoped_update', '')}"
    memory.append_history(history_entry)

    # Trim session
    session.messages = session.messages[-keep_count:]
    logger.info(f"Consolidation complete, session trimmed to {len(session.messages)} messages")
```

**Update Call Sites**:

```python
# In _process_message()
if len(session.messages) > self.memory_window:
    session_key = f"{msg.channel}:{msg.chat_id}"
    await self._consolidate_memory(session, session_key)  # Pass session_key

# Also update build_messages() call
messages = self.context.build_messages(
    history=session.get_history(),
    current_message=msg.content,
    session_key=f"{msg.channel}:{msg.chat_id}",  # NEW
    media=msg.media if msg.media else None,
    channel=msg.channel,
    chat_id=msg.chat_id,
)
```

### Component 5: Persona Drift Detection

**New Guardrails Module** (`nanobot/agent/guardrails.py`):

```python
"""Lightweight persona drift detection (warn-only)."""

import re
from loguru import logger

class PersonaDriftDetector:
    """Detect persona drift in agent responses (warn-only mode)."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._load_persona_rules()

    def _load_persona_rules(self) -> None:
        """Extract key rules from SOUL.md for drift detection."""
        soul_file = self.workspace / "SOUL.md"
        if not soul_file.exists():
            self.rules = {}
            return

        content = soul_file.read_text()

        # Extract simple keyword rules
        self.rules = {
            "forbidden_words": self._extract_forbidden_words(content),
            "required_tone": self._extract_tone(content),
            "max_length_preference": self._extract_length_preference(content),
        }

    def check_drift(
        self,
        response: str,
        session_key: str,
        mode: str = "warn"
    ) -> bool:
        """
        Check if response drifts from persona.

        Args:
            response: Agent's response text
            session_key: Current session identifier
            mode: "off" or "warn" (future: "block", "correct")

        Returns:
            True if drift detected, False otherwise
        """
        if mode == "off":
            return False

        drift_detected = False
        issues = []

        # Check 1: Forbidden words
        if self.rules.get("forbidden_words"):
            for word in self.rules["forbidden_words"]:
                if re.search(rf'\b{re.escape(word)}\b', response, re.IGNORECASE):
                    issues.append(f"Contains forbidden word: '{word}'")
                    drift_detected = True

        # Check 2: Tone mismatch (simple heuristic)
        if self.rules.get("required_tone") == "concise":
            if len(response) > 500:  # Arbitrary threshold
                issues.append(f"Response too long ({len(response)} chars) for concise tone")
                drift_detected = True

        # Log warning if drift detected
        if drift_detected and mode == "warn":
            logger.warning(
                f"⚠️ Persona drift detected in session {session_key}:\n"
                f"Issues: {', '.join(issues)}\n"
                f"Response preview: {response[:100]}..."
            )

        return drift_detected

    def _extract_forbidden_words(self, content: str) -> list[str]:
        """Simple keyword extraction (can be enhanced)."""
        # Example: look for "avoid:", "never:", etc.
        forbidden = []
        for line in content.split('\n'):
            if 'avoid' in line.lower() or 'never' in line.lower():
                # Simple extraction logic
                words = re.findall(r'\b\w+\b', line.lower())
                forbidden.extend(words)
        return list(set(forbidden))

    def _extract_tone(self, content: str) -> str | None:
        """Extract tone preference."""
        if 'concise' in content.lower():
            return "concise"
        if 'detailed' in content.lower():
            return "detailed"
        return None

    def _extract_length_preference(self, content: str) -> int | None:
        """Extract length preference."""
        # Simple heuristic
        return None
```

**Integration in AgentLoop**:

```python
class AgentLoop:
    def __init__(self, ...):
        # ... existing init ...
        self.drift_detector = PersonaDriftDetector(workspace)

    async def _process_message(self, msg: InboundMessage, session_key: str | None = None):
        # ... existing processing ...

        # After getting final_content, before returning
        if final_content and self.config.guardrails.persona_drift_mode != "off":
            self.drift_detector.check_drift(
                response=final_content,
                session_key=session_key or f"{msg.channel}:{msg.chat_id}",
                mode=self.config.guardrails.persona_drift_mode
            )

        # Return response unchanged (warn-only mode)
        return OutboundMessage(...)
```

---

## Data Flow

### Startup Flow

```
1. Load config (with new memory.* and guardrails.* fields)
2. Initialize MemoryStore
3. Check for MEMORY.md → migrate to MEMORY_GLOBAL.md if needed
4. Create memory/scoped/ directory if missing
5. Load PersonaDriftDetector rules from SOUL.md
```

### Message Processing Flow

```
Inbound Message
    ↓
1. Get/Create Session (key: "channel:chat_id")
    ↓
2. Check if consolidation needed (messages > window)
    ↓ YES
    ├─→ _consolidate_memory(session, session_key)
    │       ├─→ LLM splits into global_update + scoped_update
    │       ├─→ write_global(global_update) → MEMORY_GLOBAL.md
    │       └─→ write_scoped(session_key, scoped_update) → scoped/xxx.md
    ↓ NO / AFTER
3. Build messages with session_key
    ├─→ build_system_prompt(session_key)
    │       ├─→ Load SOUL.md + AGENTS.md (persona layer)
    │       ├─→ read_global() → MEMORY_GLOBAL.md
    │       └─→ read_scoped(session_key) → scoped/xxx.md
    └─→ Add history + current message
    ↓
4. Call LLM
    ↓
5. Execute tools (if any)
    ↓
6. Get final response
    ↓
7. Check persona drift (warn-only)
    ↓
8. Return response (unchanged)
```

### Consolidation Split Logic

```
Old Messages (100 messages from session)
    ↓
LLM Consolidation Prompt
    ├─→ "Extract GLOBAL facts (location, preferences)"
    └─→ "Extract SCOPED context (current todos, topics)"
    ↓
JSON Output:
{
  "global_update": "User lives in Beijing, prefers Python...",
  "scoped_update": "Group discussing MCP integration, todo: write tests"
}
    ↓
Write Operations:
    ├─→ memory.write_global(global_update)
    │       ├─→ check_persona_write_guard(MEMORY_GLOBAL.md) ✅ Pass
    │       └─→ Write to MEMORY_GLOBAL.md
    └─→ memory.write_scoped(session_key, scoped_update)
            ├─→ check_persona_write_guard(scoped/xxx.md) ✅ Pass
            └─→ Write to scoped/telegram_123.md
```

---

## Backward Compatibility

### Migration Path

**First Startup**:
1. Detect `MEMORY.md` exists but `MEMORY_GLOBAL.md` doesn't
2. Copy content: `MEMORY.md` → `MEMORY_GLOBAL.md`
3. Keep `MEMORY.md` for reference (mark as deprecated)

**Old Configs**:
- Configs without new fields → use defaults
- `persona_immutable=true` by default (safe)
- `scoped_enabled=true` by default (new behavior)

**Rollback Strategy**:
- Set `scoped_enabled=false` → all sessions share `MEMORY_GLOBAL.md`
- Set `persona_immutable=false` → disable write protection (not recommended)

### API Compatibility

**Deprecated but Functional**:
```python
def read_long_term(self) -> str:
    """DEPRECATED: Use read_global() instead."""
    logger.warning("read_long_term() is deprecated")
    return self.read_global()

def write_long_term(self, content: str) -> None:
    """DEPRECATED: Use write_global() instead."""
    logger.warning("write_long_term() is deprecated")
    self.write_global(content)
```

---

## Testing Strategy

### Unit Tests

**File**: `tests/test_memory_scoped_global.py`
- Test session key sanitization
- Test scoped memory read/write isolation
- Test global memory shared across sessions
- Test persona file write protection

**File**: `tests/test_persona_immutable.py`
- Test `check_persona_write_guard()` rejects persona files
- Test automatic processes cannot write to `SOUL.md` or `AGENTS.md`
- Test config flag `persona_immutable=false` disables protection

**File**: `tests/test_context_priority.py`
- Test injection order: persona → global → scoped → history
- Test persona files loaded before memory
- Test scoped memory loaded for correct session

**File**: `tests/test_persona_drift_warn.py`
- Test drift detector loads rules from `SOUL.md`
- Test drift detection logs warnings
- Test drift detection does NOT modify responses
- Test `persona_drift_mode="off"` disables detection

### Integration Tests

**Scenario 1**: Two independent sessions
```python
async def test_two_sessions_isolated():
    # Session A: 20 messages about coding
    # Session B: 20 messages about cooking

    # Verify:
    # 1. Both see same SOUL.md
    # 2. Both see same MEMORY_GLOBAL.md
    # 3. scoped/sessionA.md != scoped/sessionB.md
    # 4. Responses maintain persona despite different topics
```

**Scenario 2**: Persona protection
```python
async def test_persona_write_blocked():
    # Simulate consolidation attempting to write to SOUL.md

    # Verify:
    # 1. PermissionError raised
    # 2. Error logged with session_key
    # 3. SOUL.md content unchanged
```

**Scenario 3**: Migration
```python
async def test_legacy_migration():
    # Create old workspace with MEMORY.md
    # Start nanobot

    # Verify:
    # 1. MEMORY_GLOBAL.md created with MEMORY.md content
    # 2. MEMORY.md still exists (backward compat)
    # 3. scoped/ directory created
```

---

## Success Criteria

### Functional Requirements

✅ **Persona Consistency**
- Same question in different groups → same personality/tone
- Core rules (from `SOUL.md`) followed across all sessions
- Behavior doesn't drift based on session history

✅ **Memory Isolation**
- Group A's context doesn't appear in Group B's responses
- Each session has independent scoped memory
- Global facts shared (user location, preferences)

✅ **Write Protection**
- Automatic processes cannot modify `SOUL.md` or `AGENTS.md`
- Attempts to write persona files → error logged + exception raised
- Hash of persona files unchanged after 100+ messages

✅ **Drift Detection**
- Violations of persona rules logged with session_key
- Warnings visible in logs for debugging
- User responses not modified (warn-only mode)

### Non-Functional Requirements

✅ **Backward Compatibility**
- Old configs work without modification
- Automatic migration of `MEMORY.md` → `MEMORY_GLOBAL.md`
- No breaking changes to existing deployments

✅ **Performance**
- Consolidation time unchanged (same LLM call count)
- Scoped memory files small (<10KB typical)
- No noticeable latency increase

✅ **Maintainability**
- Clear separation of concerns (persona / global / scoped)
- Well-documented file structure
- Easy to debug memory issues

---

## Implementation Plan

### Phase 1: Configuration & Memory Store
1. Add config schema fields (`MemoryConfig`, `GuardrailsConfig`)
2. Extend `MemoryStore` with new APIs
3. Implement persona protection logic
4. Write unit tests for memory layer

### Phase 2: Context Builder
1. Refactor injection order in `build_system_prompt()`
2. Add `session_key` parameter to `build_messages()`
3. Implement persona file loading
4. Write unit tests for context building

### Phase 3: Consolidation Refactor
1. Update `_consolidate_memory()` signature (add `session_key`)
2. Modify LLM prompt to split outputs
3. Update write operations (global vs scoped)
4. Update all call sites in `_process_message()`

### Phase 4: Persona Drift Detection
1. Create `guardrails.py` module
2. Implement `PersonaDriftDetector`
3. Integrate into `AgentLoop`
4. Write unit tests for drift detection

### Phase 5: Testing & Documentation
1. Write integration tests
2. Test with real multi-session workload (20+ messages per session)
3. Update `README.md` with new memory model
4. Create migration guide
5. Document persona editing best practices

### Phase 6: Validation
1. Deploy to test environment
2. Run 2 different chat sessions with 20+ messages each
3. Verify persona consistency
4. Verify memory isolation
5. Verify `SOUL.md` hash unchanged

---

## Rollout Plan

### Stage 1: Internal Testing
- Deploy to dev environment
- Test with 5 different chat sessions
- Verify all success criteria

### Stage 2: Beta Release
- Release with `persona_immutable=true` default
- Monitor logs for drift warnings
- Collect feedback on memory isolation

### Stage 3: General Availability
- Full production release
- Documentation updated
- Migration guide published

---

## Future Enhancements

### Potential Improvements

1. **Advanced Drift Detection**
   - Use LLM-based drift scoring (compare response to `SOUL.md`)
   - Automatic drift correction (mode="correct")
   - Drift analytics dashboard

2. **Memory Compression**
   - Automatic summarization of old scoped memories
   - Archive old sessions to reduce file count
   - Memory usage quotas per session

3. **Persona Versioning**
   - Track `SOUL.md` changes with git-like history
   - A/B testing different personas
   - Rollback to previous persona versions

4. **Smart Memory Routing**
   - LLM decides which layer to update (global vs scoped)
   - Automatic fact extraction from conversations
   - Duplicate detection across layers

5. **Multi-Tenant Support**
   - Different personas for different user groups
   - Persona inheritance (base + customizations)
   - Organization-wide shared memory

---

## Conclusion

This design provides a robust foundation for persona consistency while maintaining backward compatibility. The three-layer architecture ensures:

- **Persona remains stable** across all sessions
- **Memory is cleanly separated** (facts vs context)
- **Sessions are isolated** (no cross-contamination)
- **Drift is detectable** (warn-only for now)

The implementation is incremental, testable, and low-risk. All changes are non-breaking, and the system gracefully handles legacy data.

**Next Steps**: Proceed to implementation plan creation via `writing-plans` skill.
