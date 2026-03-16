"""Tests for session history formatting."""

from nanobot.session.manager import Session


def test_get_history_prefixes_timestamp_for_user_messages() -> None:
    session = Session(
        key="cli:default",
        messages=[
            {
                "role": "user",
                "content": "hello",
                "timestamp": "2026-03-05T15:25:00",
            },
            {
                "role": "assistant",
                "content": "hi",
                "timestamp": "2026-03-05T15:25:05",
            },
        ],
    )

    history = session.get_history()

    assert history[0]["content"] == "[03-05 15:25] hello"
    assert history[1]["content"] == "hi"


def test_get_history_skips_prefix_when_timestamp_missing() -> None:
    session = Session(
        key="cli:default",
        messages=[
            {
                "role": "user",
                "content": "hello",
            }
        ],
    )

    history = session.get_history()

    assert history[0]["content"] == "hello"


def test_get_history_skips_prefix_when_timestamp_invalid() -> None:
    session = Session(
        key="cli:default",
        messages=[
            {
                "role": "user",
                "content": "hello",
                "timestamp": "not-a-timestamp",
            }
        ],
    )

    history = session.get_history()

    assert history[0]["content"] == "hello"
