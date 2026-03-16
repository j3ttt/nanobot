# Usage Tracking — Token 消耗统计

## 需求

每次 LLM 调用后，自动记录 token 消耗（含 cache 命中信息），按天聚合存储到本地 JSON 文件。支持查询今天/本周/本月的消耗和费用。

## 验收标准

1. 每次 LLM 调用后，token usage 自动累加到 `~/.nanobot/usage.json`
2. 按天聚合，key 为日期字符串（如 `2026-03-09`）
3. 记录字段：requests、prompt_tokens、completion_tokens、cache_creation_tokens、cache_read_tokens、cost_usd
4. 费用按 Anthropic 公价计算（输入 $3/M、输出 $15/M、cache 写入 $3.75/M、cache 读取 $0.30/M）
5. 零额外 token 消耗（纯代码层面统计，不调 LLM）
6. 不引入新依赖

## 现有代码分析

### 数据源：litellm response.usage

```python
# litellm.types.utils.Usage 字段：
response.usage.prompt_tokens          # 总输入 token
response.usage.completion_tokens      # 输出 token
response.usage.total_tokens           # 总计

# cache 信息在 prompt_tokens_details 里：
response.usage.prompt_tokens_details.cached_tokens           # cache 命中读取的 token（OpenAI 风格）
response.usage.prompt_tokens_details.cache_creation_tokens   # cache 写入的 token（Anthropic 专用）
```

### 当前代码流程

1. `providers/litellm_provider.py` `_parse_response()` — 已提取 prompt/completion/total，但丢弃了 cache 字段
2. `providers/base.py` `LLMResponse.usage` — `dict[str, int]`，当前只有 3 个 key
3. `agent/loop.py` `_process_message()` — 调用 `provider.chat()` 拿到 response，但未对 usage 做任何处理

## 改动方案

### 1. `providers/litellm_provider.py` — 补全 cache 字段

在 `_parse_response()` 的 usage 提取部分，增加 cache 字段：

```python
usage = {}
if hasattr(response, "usage") and response.usage:
    usage = {
        "prompt_tokens": response.usage.prompt_tokens or 0,
        "completion_tokens": response.usage.completion_tokens or 0,
        "total_tokens": response.usage.total_tokens or 0,
    }
    # Extract cache info from prompt_tokens_details
    details = getattr(response.usage, "prompt_tokens_details", None)
    if details:
        usage["cache_read_tokens"] = getattr(details, "cached_tokens", 0) or 0
        usage["cache_creation_tokens"] = getattr(details, "cache_creation_tokens", 0) or 0
```

### 2. 新建 `nanobot/agent/usage.py` — UsageTracker

```python
"""Token usage tracker — per-day aggregation to ~/.nanobot/usage.json"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# Anthropic pricing (USD per million tokens)
PRICING = {
    "prompt": 3.0,
    "completion": 15.0,
    "cache_creation": 3.75,
    "cache_read": 0.30,
}


class UsageTracker:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".nanobot" / "usage.json"
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def record(self, usage: dict[str, int]) -> None:
        """Accumulate one LLM call's usage into today's bucket."""
        if not usage:
            return
        today = date.today().isoformat()  # "2026-03-09"
        bucket = self._data.setdefault(today, {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
        })
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        cache_creation = usage.get("cache_creation_tokens", 0)
        cache_read = usage.get("cache_read_tokens", 0)

        bucket["requests"] += 1
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        bucket["cache_creation_tokens"] += cache_creation
        bucket["cache_read_tokens"] += cache_read

        # Calculate cost for this call
        cost = (
            (prompt - cache_read - cache_creation) * PRICING["prompt"] / 1_000_000
            + completion * PRICING["completion"] / 1_000_000
            + cache_creation * PRICING["cache_creation"] / 1_000_000
            + cache_read * PRICING["cache_read"] / 1_000_000
        )
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
        self._save()

    def query(self, days: int = 1) -> dict[str, Any]:
        """Aggregate usage over the last N days."""
        result = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
            "days": {},
        }
        today = date.today()
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            if d in self._data:
                bucket = self._data[d]
                for key in ["requests", "prompt_tokens", "completion_tokens",
                            "cache_creation_tokens", "cache_read_tokens"]:
                    result[key] += bucket.get(key, 0)
                result["cost_usd"] += bucket.get("cost_usd", 0.0)
                result["days"][d] = bucket
        result["cost_usd"] = round(result["cost_usd"], 6)
        return result
```

### 3. `agent/loop.py` — 接入 UsageTracker

在 `__init__` 中初始化 tracker：

```python
from nanobot.agent.usage import UsageTracker
self.usage_tracker = UsageTracker()
```

在每次 `provider.chat()` 返回后，记录 usage：

```python
response = await self.provider.chat(...)
self.usage_tracker.record(response.usage)
```

需要在 `_process_message` 的 agent loop 里每次 LLM 调用后都加（包括 tool call 循环中的每次调用）。

## 文件变更清单

| 文件 | 改动 |
|---|---|
| `nanobot/providers/litellm_provider.py` | `_parse_response()` 补全 cache 字段 |
| `nanobot/agent/usage.py` | 新建，UsageTracker 类 |
| `nanobot/agent/loop.py` | 初始化 tracker + 每次 chat 后 record |

## 存储格式

```json
{
  "2026-03-09": {
    "requests": 47,
    "prompt_tokens": 580000,
    "completion_tokens": 12000,
    "cache_creation_tokens": 200000,
    "cache_read_tokens": 380000,
    "cost_usd": 2.35
  }
}
```

## 查询方式

agent 读 `~/.nanobot/usage.json` 即可回答用户的消耗查询，不需要额外工具。

## 费用计算公式

```
cost = (prompt - cache_read - cache_creation) * $3/M
     + completion * $15/M
     + cache_creation * $3.75/M
     + cache_read * $0.30/M
```

注意：prompt_tokens 是总输入 token，已包含 cache_read 和 cache_creation 部分，所以计算"正常价格输入"时需要减去。
