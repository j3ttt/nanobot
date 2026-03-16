# Spec: Curiosity Ping（好奇心 Ping）

> Created: 2026-03-16
> Status: Implementing

## 需求

用户（小飞机）希望 agent 在长时间未收到消息时，主动发一条消息表达好奇——"你跑哪去了"风格，而不是机械的"您好，请问有什么可以帮您"。

## 行为定义

1. 每次收到用户消息后，**重置**一个 2~8 小时的随机倒计时
2. 倒计时到期且未收到新消息 → agent 主动向该 session 的 channel/chat_id 发一条消息
3. 只触发一次，不连环追问
4. 如果用户最后一条消息的意图是**睡觉**（晚安、睡了、good night、going to bed 等），不启动倒计时

## 技术方案

### 核心机制：复用 CronService

不新建定时器系统，直接用已有的 cron job 机制：

- Job 命名约定：`curiosity-ping:{session_key}`（方便按 session 查找和取消）
- Schedule：`kind="at"`，`at_ms` = 当前时间 + random(2h, 8h)
- `delete_after_run=True`（一次性）
- Payload：`deliver=True`，`channel` 和 `to` 从当前消息继承

### 关键设计：session_key 传递

cron job 触发时需要用**原始 session**（如 `dingtalk:cidXXX`）而不是 `cron:{job.id}`，这样 LLM 能看到最近的聊天记录，才能自然地接话。

方案：给 CronPayload 新增 `session_key` 字段，`on_cron_job` 回调优先使用它。

### 修改点

#### 1. `nanobot/cron/types.py` — CronPayload 新增字段

```python
@dataclass
class CronPayload:
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    message: str = ""
    deliver: bool = False
    channel: str | None = None
    to: str | None = None
    session_key: str | None = None  # NEW: override session_key for process_direct
```

#### 2. `nanobot/cron/service.py` — _load_store / _save_store 序列化新字段

在 load 时读取 `session_key`，save 时写入。

#### 3. `nanobot/cli/commands.py` — on_cron_job 回调使用 session_key

```python
async def on_cron_job(job: CronJob) -> str | None:
    response = await agent.process_direct(
        job.payload.message,
        session_key=job.payload.session_key or f"cron:{job.id}",  # CHANGED
        channel=job.payload.channel or "cli",
        chat_id=job.payload.to or "direct",
    )
    if job.payload.deliver and job.payload.to:
        await bus.publish_outbound(
            OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response or "",
            )
        )
    return response
```

#### 4. `nanobot/agent/loop.py` — 新增 curiosity ping 逻辑

在 `_process_message()` 末尾，`sessions.save()` 之后：

```python
# Curiosity ping: reset timer on every user message
if msg.channel != "system":
    self._reset_curiosity_ping(msg)
```

新增方法：

```python
import random
import re
import time

_SLEEP_PATTERNS = re.compile(
    r"(晚安|睡了|睡觉|去睡|good\s*night|going to (bed|sleep)|gn\b|nighty?\s*night)",
    re.IGNORECASE,
)

def _reset_curiosity_ping(self, msg: InboundMessage) -> None:
    """Reset the curiosity ping timer for this session."""
    if not self.cron_service:
        return

    session_key = msg.session_key
    job_name = f"curiosity-ping:{session_key}"

    # 1. Remove existing curiosity ping for this session
    for job in self.cron_service.list_jobs(include_disabled=True):
        if job.name == job_name:
            self.cron_service.remove_job(job.id)

    # 2. Sleep intent → don't create new job
    if _SLEEP_PATTERNS.search(msg.content):
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
```

#### 5. `nanobot/cron/service.py` — add_job 接受 session_key

```python
def add_job(self, ..., session_key: str | None = None, ...) -> CronJob:
    ...
    payload=CronPayload(
        kind="agent_turn",
        message=message,
        deliver=deliver,
        channel=channel,
        to=to,
        session_key=session_key,
    ),
```

### 不改动的部分

- Session 存储不变
- 不新增配置项（后续可加开关）
- CronTool（agent 工具侧）不需要改，curiosity ping 是内部逻辑

## 验收标准

1. 发消息后 2-8h 内如果没有新消息，收到一条自然风格的主动消息
2. 发"晚安"后不会被打扰
3. 连续发多条消息只保留最后一条的倒计时（旧的被取消）
4. 只 ping 一次，不会连环发
5. 重启后 cron job 从磁盘恢复，倒计时不丢失
6. 主动消息能引用最近聊天内容（因为用了原始 session）

## 风险 & 注意

- macOS 睡眠时 asyncio timer 冻结，但 CronService 已有 watchdog 机制（30s 轮询）可兜底
- 多 session 场景：每个 session 独立一个 job，互不干扰
- job message 是给 LLM 的 prompt，LLM 自由发挥内容，风格由 system prompt 人设控制
