#!/usr/bin/env python3
"""
Info Radar — 个人信息雷达
拉取 RSS feeds，去重，输出新条目 JSON 到 stdout。
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

DEFAULT_TIMEOUT = 15  # seconds per feed
USER_AGENT = "InfoRadar/1.0 (nanobot skill)"


def load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def hash_link(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def parse_entry_time(entry) -> datetime | None:
    """Try to extract a timezone-aware datetime from a feed entry."""
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            try:
                return datetime(*tp[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    return None


def fetch_feed(feed_cfg: dict, cutoff: datetime, seen: set) -> list[dict]:
    """Fetch a single feed and return new items after cutoff."""
    url = feed_cfg["url"]
    name = feed_cfg["name"]
    category = feed_cfg.get("category", "Uncategorized")

    try:
        d = feedparser.parse(
            url,
            agent=USER_AGENT,
            request_headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
        )
    except Exception as e:
        print(f"[ERROR] {name}: {e}", file=sys.stderr)
        return []

    if d.bozo and not d.entries:
        print(f"[WARN] {name}: feed parse error — {d.bozo_exception}", file=sys.stderr)
        return []

    items = []
    for entry in d.entries:
        link = getattr(entry, "link", "") or ""
        if not link:
            continue

        h = hash_link(link)
        if h in seen:
            continue

        pub_time = parse_entry_time(entry)
        if pub_time and pub_time < cutoff:
            continue

        # Extract summary, strip HTML tags roughly
        summary = getattr(entry, "summary", "") or ""
        if len(summary) > 500:
            summary = summary[:500] + "..."

        items.append({
            "title": getattr(entry, "title", "(no title)"),
            "link": link,
            "source": name,
            "category": category,
            "published": pub_time.isoformat() if pub_time else None,
            "summary": summary,
        })
        seen.add(h)

    return items


def main():
    parser = argparse.ArgumentParser(description="Info Radar — fetch & dedupe RSS feeds")
    parser.add_argument("--feeds", type=str, default=None, help="Path to feeds.json")
    parser.add_argument("--seen", type=str, default=None, help="Path to seen.json (dedup store)")
    parser.add_argument("--hours", type=int, default=12, help="Only fetch items from last N hours")
    args = parser.parse_args()

    # Resolve paths relative to script location
    script_dir = Path(__file__).parent
    feeds_path = Path(args.feeds) if args.feeds else script_dir / "feeds.json"
    seen_path = Path(args.seen) if args.seen else script_dir / "seen.json"

    if not feeds_path.exists():
        print(f"[FATAL] feeds.json not found: {feeds_path}", file=sys.stderr)
        sys.exit(1)

    feeds_cfg = load_json(feeds_path)
    feeds = [f for f in feeds_cfg.get("feeds", []) if f.get("enabled", True)]

    if not feeds:
        print("[FATAL] No enabled feeds found", file=sys.stderr)
        sys.exit(1)

    # Load seen hashes
    seen_data = load_json(seen_path, default={"hashes": [], "last_cleanup": None})
    seen_set = set(seen_data.get("hashes", []))
    old_count = len(seen_set)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    all_items = []
    for feed in feeds:
        print(f"[INFO] Fetching: {feed['name']}...", file=sys.stderr)
        items = fetch_feed(feed, cutoff, seen_set)
        all_items.extend(items)
        print(f"[INFO] {feed['name']}: {len(items)} new items", file=sys.stderr)

    # Sort by published time (newest first), None at the end
    all_items.sort(key=lambda x: x["published"] or "", reverse=True)

    # Save seen hashes (keep last 10000 to avoid unbounded growth)
    seen_list = list(seen_set)
    if len(seen_list) > 10000:
        seen_list = seen_list[-10000:]
    save_json(seen_path, {
        "hashes": seen_list,
        "last_cleanup": datetime.now(timezone.utc).isoformat(),
    })

    # Output
    result = {
        "fetch_time": datetime.now(timezone.utc).isoformat(),
        "hours": args.hours,
        "total_new": len(all_items),
        "sources_fetched": len(feeds),
        "items": all_items,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
