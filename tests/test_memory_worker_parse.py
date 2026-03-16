"""Tests for MemoryWorker._parse_response delimiter-based parsing."""

from nanobot.agent.memory_worker import MemoryWorker


def make_worker():
    """Create a minimal MemoryWorker-like object for testing _parse_response."""
    return None  # _parse_response only uses self for logger, which is module-level


def test_clean_delimiters():
    text = (
        "===HISTORY===\n"
        "[2026-03-03 18:13] User discussed meme skill rewrite and Yuque image scraping.\n"
        "===MEMORY===\n"
        "## User Info\n"
        "- Name: xiaofei\n"
        "===END==="
    )
    r = MemoryWorker._parse_response(None, text)
    assert "meme skill" in r["history_entry"], f"history failed: {r}"
    assert "xiaofei" in r["memory_update"], f"memory failed: {r}"
    print("Test 1 (clean delimiters): PASS")


def test_special_chars():
    text = (
        "===HISTORY===\n"
        '[2026-03-04 15:00] User asked about "streaming" in DingTalk. Code: json.loads(text)\n'
        "===MEMORY===\n"
        "## Known Issues\n"
        "- DingTalk no streaming\n"
        "- config uses camelCase\n"
        "===END==="
    )
    r = MemoryWorker._parse_response(None, text)
    assert "streaming" in r["history_entry"], f"history failed: {r}"
    assert "camelCase" in r["memory_update"], f"memory failed: {r}"
    print("Test 2 (special chars): PASS")


def test_json_fallback():
    text = '{"history_entry": "some summary", "memory_update": "some facts"}'
    r = MemoryWorker._parse_response(None, text)
    assert r["history_entry"] == "some summary", f"failed: {r}"
    print("Test 3 (JSON fallback): PASS")


def test_garbage():
    text = "The LLM went rogue and returned nonsense"
    r = MemoryWorker._parse_response(None, text)
    assert r == {"history_entry": "", "memory_update": ""}, f"failed: {r}"
    print("Test 4 (garbage): PASS")


def test_no_end_delimiter():
    text = (
        "===HISTORY===\n"
        "Some history here\n"
        "===MEMORY===\n"
        "Some memory here that goes on"
    )
    r = MemoryWorker._parse_response(None, text)
    assert r["history_entry"], f"history empty: {r}"
    assert r["memory_update"], f"memory empty: {r}"
    print("Test 5 (no END delimiter): PASS")


if __name__ == "__main__":
    test_clean_delimiters()
    test_special_chars()
    test_json_fallback()
    test_garbage()
    test_no_end_delimiter()
    print("\nAll tests passed!")
