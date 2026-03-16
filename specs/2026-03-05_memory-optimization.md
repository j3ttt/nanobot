# Memory Optimization: Timestamped History + Background Memory Worker

- Date: 2026-03-05
- Status: Draft → 待确认
- Author: jettt 🐈

## Problem

当前 session 管理存在两个问题导致上下文混乱：

1. `Session.get_history()` 丢弃时间戳，LLM 无法区分"昨天说的下班"和"刚才说的话"
2. Memory consolidation 只在 `memory_window`（默认 50）溢出时触发，个人助手场景下一天 10-20 条消息，永远触发不了，导致跨天对话堆积

实际案例：2026-03-05 下午的对话中，LLM 把 03-04 晚上的"下班了"误当成刚说的，因为 20 条消息跨两天全部无时间标记地塞给了 LLM。

## Solution Overview

分两个 Phase：

- Phase 1: get_history() 返回时间戳（小改动，立竿见影）
- Phase 2: 新增 Memory Backend Worker（独立后台 LLM 任务，定期处理记忆）

---

## Phase 1: Timestamped History

### 改动文件

`nanobot/session/manager.py` → `Session.get_history()`

### 当前行为

```python
def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
    recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
    # 只返回 role + content，timestamp 被丢弃
    return [{"role": m["role"], "content": m["content"]} for m in recent]
```

### 目标行为

在 content 前面拼上时间戳，让 LLM 能感知时间间隔：

```python
def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
    recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
    result = []
    for m in recent:
        content = m.get("content", "")
        ts = m.get("timestamp", "")
        if ts and m["role"] == "user":
            # 只给 user 消息加时间戳，assistant 消息不需要
            # 格式: [03-05 15:25] 原始内容
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(ts)
                prefix = dt.strftime("[%m-%d %H:%M] ")
                content = prefix + content
            except (ValueError, TypeError):
                pass
        result.append({"role": m["role"], "content": content})
    return result
```

### 设计决策

- 只给 user 消息加时间戳：assistant 消息的时间戳意义不大，减少 token 消耗
- 格式 `[MM-DD HH:MM]`：简洁，LLM 容易理解，grep 友好
- 解析失败时静默跳过：不影响现有功能

### 验收标准

- [ ] 跨天对话中，LLM 能正确识别时间间隔（不再把昨天的消息当成刚说的）
- [ ] 现有功能不受影响（工具调用、consolidation 等）
- [ ] 单元测试覆盖：有时间戳 / 无时间戳 / 格式异常三种 case

---

## Phase 2: Memory Backend Worker

### 动机

当前 consolidation 的问题：
1. 只在 `memory_window` 溢出时触发，阈值太高（50），个人场景很难触发
2. 在主对话流程中同步运行，用主力模型（贵），阻塞消息处理
3. 没有时间感知，不会因为"隔了一夜"就自动归档

### 架构

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  AgentLoop   │────▶│  MessageBus  │     │  MemoryWorker    │
│  (主对话)    │     │              │     │  (后台 asyncio)  │
└─────────────┘     └──────────────┘     │                  │
                                          │  - 定时检查 session│
                                          │  - 用廉价模型摘要  │
                                          │  - 写 HISTORY.md  │
                                          │  - 更新 MEMORY.md │
                                          │  - 裁剪 session   │
                                          └──────────────────┘
```

### 新增文件

`nanobot/agent/memory_worker.py`

### 核心类

```python
class MemoryWorker:
    """
    后台记忆处理 worker。
    独立于主对话循环，定期检查并处理 session 记忆。
    """
    
    def __init__(
        self,
        session_manager: SessionManager,
        memory_store: MemoryStore,
        provider: LLMProvider,
        model: str = "anthropic/claude-haiku",  # 用廉价模型
        check_interval: int = 300,               # 每 5 分钟检查一次
        time_gap_threshold: int = 14400,          # 4 小时无活动触发归档
        message_threshold: int = 20,              # 消息数超过此值也触发
    ):
        ...
    
    async def run(self) -> None:
        """主循环：定期检查所有 session"""
        while self._running:
            await self._check_all_sessions()
            await asyncio.sleep(self.check_interval)
    
    async def _check_all_sessions(self) -> None:
        """遍历所有活跃 session，判断是否需要 consolidation"""
        for session in self._get_active_sessions():
            if self._should_consolidate(session):
                await self._consolidate(session)
    
    def _should_consolidate(self, session: Session) -> bool:
        """
        判断是否需要归档，触发条件（满足任一即可）：
        1. 最后一条消息距今超过 time_gap_threshold（默认 4 小时）
        2. 消息总数超过 message_threshold（默认 20 条）
        3. 检测到跨天（最早消息和最新消息不在同一天）
        """
        ...
    
    async def _consolidate(self, session: Session) -> None:
        """
        执行归档：
        1. 保留最近 N 条消息（默认 6 条，即最近 3 轮对话）
        2. 旧消息送给廉价 LLM 生成摘要
        3. 摘要写入 HISTORY.md
        4. 从摘要中提取长期事实更新 MEMORY.md
        5. 裁剪 session 并保存
        """
        ...
```

### 触发条件详解

| 条件 | 默认值 | 说明 |
|------|--------|------|
| 时间间隔 | 4 小时 | 最后一条消息距今超过 4h，说明对话已结束 |
| 消息数量 | 20 条 | 不管时间，消息太多就归档 |
| 跨天检测 | - | 最早和最新消息不在同一天 |

三个条件是 OR 关系，满足任一即触发。

### 与现有 consolidation 的关系

- 现有的 `AgentLoop._consolidate_memory()` 保留，作为"兜底"——万一 worker 没跑或挂了，主循环里还有最后防线
- Worker 的阈值更低（20 vs 50），会更早介入
- Worker 用廉价模型（haiku），主循环用主力模型（sonnet）

### 配置

在 `config/schema.py` 的 `AgentDefaults` 中新增：

```python
class MemoryWorkerConfig(BaseModel):
    """Memory background worker configuration."""
    enabled: bool = True
    model: str = "anthropic/claude-haiku"    # 廉价模型
    check_interval: int = 300                 # 秒，检查间隔
    time_gap_threshold: int = 14400           # 秒，4 小时
    message_threshold: int = 20               # 消息数阈值
    keep_recent: int = 6                      # 归档后保留的消息数
```

### 启动集成

在 `nanobot/__main__.py` 或 app 启动处，和 AgentLoop 一起启动：

```python
memory_worker = MemoryWorker(
    session_manager=session_manager,
    memory_store=memory_store,
    provider=provider,
    model=config.agents.memory_worker.model,
)
asyncio.create_task(memory_worker.run())
```

### 验收标准

- [ ] Worker 能在后台独立运行，不阻塞主对话
- [ ] 跨天对话被自动归档，session 文件被裁剪
- [ ] HISTORY.md 有新增的摘要条目
- [ ] MEMORY.md 中新事实被正确提取
- [ ] Worker 挂掉不影响主对话（优雅降级）
- [ ] 配置可关闭（enabled: false）
- [ ] 日志中可观测 worker 的运行状态

---

## 实施计划

| Phase | 改动量 | 预计耗时 | 风险 |
|-------|--------|----------|------|
| Phase 1: Timestamped History | ~15 行 | 10 分钟 | 极低 |
| Phase 2: Memory Worker | ~200 行新文件 + ~30 行集成 | 1-2 小时 | 中（需测试并发安全） |

建议 Phase 1 直接改，Phase 2 用 codex 处理。

---

## Open Questions

1. Worker 的廉价模型选什么？haiku 最便宜但质量一般，mini 也行。或者用 dashscope/qwen 更便宜？
2. 归档后保留几条消息？6 条（3 轮对话）够不够？
3. 要不要在归档时给用户发个通知（比如"我整理了一下记忆"）？还是完全静默？
4. Session 文件要不要做备份再裁剪？防止 consolidation 出错丢数据。
