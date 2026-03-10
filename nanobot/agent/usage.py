"""Token usage tracker — per-day aggregation to ~/.nanobot/usage.json"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

# Anthropic pricing (USD per million tokens)
PRICING = {
    "prompt": 3.0,
    "completion": 15.0,
    "cache_creation": 3.75,
    "cache_read": 0.30,
}


class UsageTracker:
    """Track token usage per day, persist to a JSON file."""

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

        today = date.today().isoformat()
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

        # prompt_tokens already includes cache_read + cache_creation,
        # so "normal price" input = prompt - cache_read - cache_creation
        normal_input = max(0, prompt - cache_read - cache_creation)
        cost = (
            normal_input * PRICING["prompt"] / 1_000_000
            + completion * PRICING["completion"] / 1_000_000
            + cache_creation * PRICING["cache_creation"] / 1_000_000
            + cache_read * PRICING["cache_read"] / 1_000_000
        )
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)

        self._save()

        logger.debug(
            f"Usage recorded: prompt={prompt} completion={completion} "
            f"cache_create={cache_creation} cache_read={cache_read} "
            f"cost=${cost:.4f}"
        )

    def query(self, days: int = 1) -> dict[str, Any]:
        """Aggregate usage over the last N days."""
        self._data = self._load()  # reload for freshness
        result: dict[str, Any] = {
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
                for key in [
                    "requests", "prompt_tokens", "completion_tokens",
                    "cache_creation_tokens", "cache_read_tokens",
                ]:
                    result[key] += bucket.get(key, 0)
                result["cost_usd"] += bucket.get("cost_usd", 0.0)
                result["days"][d] = bucket

        result["cost_usd"] = round(result["cost_usd"], 6)
        return result
