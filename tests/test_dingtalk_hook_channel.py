import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.dingtalk_hook import DingTalkHookChannel
from nanobot.config.schema import DingTalkHookConfig


@pytest.mark.asyncio
async def test_poll_once_forwards_receive_message(monkeypatch) -> None:
    cfg = DingTalkHookConfig(enabled=True)
    channel = DingTalkHookChannel(cfg, MessageBus())

    captured = {"called": False, "content": "", "chat_id": ""}

    async def _fake_fetch():
        return [
            {"type": "receive", "cid": "1001:1002", "texts": ["你好", "今天有空吗"]},
            {"type": "send", "cid": "1001:1002", "text": "ignore"},
        ]

    async def _fake_handle_message(*args, **kwargs):
        captured["called"] = True
        captured["content"] = kwargs["content"]
        captured["chat_id"] = kwargs["chat_id"]

    monkeypatch.setattr(channel, "_fetch_recent_messages", _fake_fetch)
    monkeypatch.setattr(channel, "_handle_message", _fake_handle_message)

    await channel._poll_once()

    assert captured["called"] is True
    assert captured["chat_id"] == "1001:1002"
    assert captured["content"] == "你好 | 今天有空吗"


@pytest.mark.asyncio
async def test_low_value_message_is_dropped_with_auto_reply(monkeypatch) -> None:
    cfg = DingTalkHookConfig(
        enabled=True,
        firewall_enabled=True,
        firewall_low_value_keywords=["在吗"],
        firewall_auto_reply_low_value=True,
        firewall_low_value_reply_text="收到，稍后回复",
    )
    channel = DingTalkHookChannel(cfg, MessageBus())

    handled = {"called": False}
    sent = {"called": False, "cid": "", "text": ""}

    async def _fake_fetch():
        return [{"type": "receive", "cid": "u123", "texts": ["在吗"]}]

    async def _fake_handle_message(*args, **kwargs):
        handled["called"] = True

    async def _fake_send(msg: OutboundMessage):
        sent["called"] = True
        sent["cid"] = msg.chat_id
        sent["text"] = msg.content

    monkeypatch.setattr(channel, "_fetch_recent_messages", _fake_fetch)
    monkeypatch.setattr(channel, "_handle_message", _fake_handle_message)
    monkeypatch.setattr(channel, "send", _fake_send)

    await channel._poll_once()

    assert handled["called"] is False
    assert sent["called"] is True
    assert sent["cid"] == "u123"
    assert sent["text"] == "收到，稍后回复"


@pytest.mark.asyncio
async def test_high_risk_message_is_prefixed(monkeypatch) -> None:
    cfg = DingTalkHookConfig(
        enabled=True,
        firewall_enabled=True,
        firewall_high_risk_keywords=["合同"],
        firewall_risk_prefix="[高风险沟通预警]",
    )
    channel = DingTalkHookChannel(cfg, MessageBus())

    captured = {"content": "", "tags": []}

    async def _fake_fetch():
        return [{"type": "receive", "cid": "u123", "texts": ["这个合同今天要确认"]}]

    async def _fake_handle_message(*args, **kwargs):
        captured["content"] = kwargs["content"]
        captured["tags"] = kwargs["metadata"]["firewall_tags"]

    monkeypatch.setattr(channel, "_fetch_recent_messages", _fake_fetch)
    monkeypatch.setattr(channel, "_handle_message", _fake_handle_message)

    await channel._poll_once()

    assert captured["content"].startswith("[高风险沟通预警]")
    assert captured["tags"] == ["high_risk"]
