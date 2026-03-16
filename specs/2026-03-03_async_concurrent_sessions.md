# Spec: Per-Session Concurrent Message Processing

## Problem

当前 `AgentLoop.run()` 是严格单线程串行的：
1. 从 `bus.consume_inbound()` 取一条消息
2. `await self._process_message(msg)` 处理（包含多轮 LLM 调用 + 工具执行，耗时几十秒到几分钟）
3. 处理完才取下一条

导致：不同 channel/chat 的消息互相阻塞。用户 A 在钉钉群聊发消息，用户 B 在私聊发消息，B 必须等 A 处理完才能得到回复。

## Goal

实现 per-session 并发：
- 同一 session（同一 channel:chat_id）内的消息串行处理（保证上下文一致性）
- 不同 session 之间并行处理（互不阻塞）

## Architecture

### 核心改动：AgentLoop.run()

将当前的 sequential loop 改为 dispatcher + per-session worker 模式：

```
┌─────────────┐
│  inbound Q  │
└──────┬──────┘
       │ consume
       ▼
┌──────────────┐
│  dispatcher  │  (main loop, lightweight)
└──────┬───────┘
       │ route by session_key
       ▼
┌──────────────────────────────────┐
│  session workers (dict)          │
│                                  │
│  "dingtalk:cidXXX" → worker A    │
│  "dingtalk:cidYYY" → worker B    │
│  "imessage:+86..." → worker C   │
│                                  │
│  Each worker has its own Queue   │
│  and processes messages serially │
└──────────────────────────────────┘
```

### 新增类：SessionWorker

```python
class SessionWorker:
    """Per-session message processor."""
    
    def __init__(self, session_key: str, agent_loop: AgentLoop):
        self.session_key = session_key
        self.queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.agent_loop = agent_loop
        self._task: asyncio.Task | None = None
        self._idle_since: float | None = None
    
    async def run(self):
        """Process messages for this session serially."""
        while True:
            try:
                msg = await asyncio.wait_for(self.queue.get(), timeout=300)  # 5min idle timeout
                self._idle_since = None
                try:
                    response = await self.agent_loop._process_message(msg)
                    if response:
                        await self.agent_loop.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message in session {self.session_key}: {e}")
                    await self.agent_loop.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"Sorry, I encountered an error: {str(e)}",
                        )
                    )
            except asyncio.TimeoutError:
                # Idle too long, exit and let dispatcher clean up
                self._idle_since = time.time()
                break
    
    def enqueue(self, msg: InboundMessage):
        self.queue.put_nowait(msg)
    
    def start(self):
        self._task = asyncio.create_task(self.run())
    
    @property
    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()
```

### 改动：AgentLoop.run()

```python
async def run(self) -> None:
    self._running = True
    self._session_workers: dict[str, SessionWorker] = {}
    
    if self.mcp_config and self.mcp_config.get("enabled"):
        await self._initialize_mcp()
    
    while self._running:
        try:
            msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            
            # Determine session key
            if msg.channel == "system":
                # System messages (subagent results) need special routing
                # They contain the original session key in metadata
                session_key = self._get_system_message_session_key(msg)
            else:
                session_key = msg.session_key
            
            # Get or create worker for this session
            worker = self._session_workers.get(session_key)
            if worker is None or not worker.is_alive:
                worker = SessionWorker(session_key, self)
                self._session_workers[session_key] = worker
                worker.start()
            
            worker.enqueue(msg)
            
            # Periodic cleanup of dead workers
            self._cleanup_dead_workers()
            
        except asyncio.TimeoutError:
            continue

def _cleanup_dead_workers(self):
    dead = [k for k, w in self._session_workers.items() if not w.is_alive]
    for k in dead:
        del self._session_workers[k]
```

## Constraints

1. `_process_message` 本身不需要改动，它已经是 async 的
2. SessionManager（session 文件读写）目前是同步 JSON 操作，需要确认是否线程安全。如果不安全，加一个 per-session asyncio.Lock
3. ToolRegistry 是共享的，但各工具的 `set_context()` 调用在 `_process_message` 里——这是一个竞争条件！需要修复
4. MessageTool、SpawnTool、CronTool 的 `set_context()` 在并发下会互相覆盖

### 关键竞争条件修复：Tool Context

当前问题：
```python
# _process_message 里
message_tool.set_context(msg.channel, msg.chat_id)  # 设置 context
# ... 然后 LLM 调用 + 工具执行
# 但另一个 session 的 _process_message 可能在中间覆盖了 context！
```

解决方案：将 context 从工具实例上移到 per-call 参数上。

方案 A（最小改动）：在 `_process_message` 开头 clone 一份 tool registry，每个 session 用自己的副本。
方案 B（更干净）：把 channel/chat_id 作为执行上下文传递，而不是存在工具实例上。

推荐方案 A，因为改动最小：
```python
async def _process_message(self, msg, session_key=None):
    # Clone tools for this session to avoid context conflicts
    session_tools = self.tools.clone()
    # set_context on the cloned tools
    message_tool = session_tools.get("message")
    if isinstance(message_tool, MessageTool):
        message_tool.set_context(msg.channel, msg.chat_id)
    # ... use session_tools instead of self.tools
```

ToolRegistry 需要新增 `clone()` 方法，对有状态的工具（MessageTool, SpawnTool, CronTool）做深拷贝，无状态工具共享即可。

## Testing

1. 单元测试：两个不同 session_key 的消息同时入队，验证并行处理
2. 单元测试：同一 session_key 的两条消息，验证串行处理（第二条等第一条完成）
3. 单元测试：worker idle 超时后自动清理
4. 集成测试：钉钉群聊 + 私聊同时发消息，验证互不阻塞

## Acceptance Criteria

- [ ] 不同 session 的消息并行处理
- [ ] 同一 session 的消息串行处理
- [ ] 工具 context 不会跨 session 污染
- [ ] 现有所有测试通过
- [ ] worker 空闲 5 分钟后自动回收
- [ ] system 消息（subagent results）正确路由到原始 session
