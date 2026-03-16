# Structured Signal — LLM 内联元数据协议

Created: 2026-03-16
Status: implementing

## 概述

让 LLM 在每次回复末尾附带一个 HTML 注释块，携带结构化信号（JSON）。
代码层提取信号后剥离注释，用户看不到。

## 格式

```
正常回复内容

<!--signals
{"sleep_intent": false}
-->
```

## 为什么用 HTML 注释

- IM 端 Markdown 渲染器直接忽略，用户无感
- 解析简单，一行正则搞定
- 比 function calling 轻量，不占工具调用开销
- 即使 LLM 偶尔忘了输出，系统照常运行（优雅降级）

## 当前信号字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `sleep_intent` | bool | 用户是否表达了睡觉/离开意图，用于跳过 curiosity ping |

## 未来可扩展字段（预留）

| 字段 | 类型 | 说明 |
|------|------|------|
| `mood` | string | 用户当前情绪判断 |
| `urgency` | string | 消息紧急程度 (low/medium/high) |
| `topic` | string | 当前话题分类 |
| `follow_up` | bool | 是否需要后续跟进 |

## 改动清单

### 1. context.py — system prompt 加信号输出指令

在 `_get_identity()` 的 nanobot Guidelines 末尾追加：

```
## Structured Signals
At the end of EVERY reply, append a hidden signal block. The user will not see it.
Format (MUST be the last thing in your reply):

<!--signals
{"sleep_intent": false}
-->

- sleep_intent: set to true if the user expressed intent to sleep, go to bed, or sign off for the night.
- ALWAYS include this block, even if all values are false/default.
- The block MUST be valid JSON inside an HTML comment.
```

### 2. loop.py — extract_signals() 提取 + 剥离

```python
_SIGNAL_RE = re.compile(r"<!--signals\s*\n(\{.*?\})\s*\n-->", re.DOTALL)

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
        signals = {}
    cleaned = content[:match.start()].rstrip()
    return cleaned, signals
```

### 3. loop.py — _process_message 中集成

在 final_content 确定后、保存 session 和发送前：

```python
# Extract structured signals from LLM response
final_content, signals = self._extract_signals(final_content)

# ... save to session with cleaned content ...

# Curiosity ping: use signal instead of regex
if msg.channel != "system":
    self._reset_curiosity_ping(msg, signals)
```

### 4. loop.py — _reset_curiosity_ping 改用 signals

```python
def _reset_curiosity_ping(self, msg: InboundMessage, signals: dict | None = None) -> None:
    # ... remove existing job ...
    
    # Sleep intent: prefer signal, fallback to regex
    sleep_intent = False
    if signals and signals.get("sleep_intent"):
        sleep_intent = True
    elif self._SLEEP_PATTERNS.search(msg.content):
        sleep_intent = True
    
    if sleep_intent:
        logger.debug("Curiosity ping skipped: sleep intent detected")
        return
    
    # ... create new job ...
```

## 优雅降级

- LLM 忘了输出信号块 → `_extract_signals` 返回空 dict → fallback 到正则匹配
- LLM 输出了非法 JSON → 同上，静默降级
- 信号块出现在回复中间（不在末尾）→ 正则仍能匹配，但只剥离到信号块之前的内容

## 零额外成本

信号判断在已有的 LLM 调用中完成，仅多输出约 40 token 的 HTML 注释块。
