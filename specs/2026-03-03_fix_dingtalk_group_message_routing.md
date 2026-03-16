# 2026-03-03 Fix DingTalk Group Message Routing

## 需求描述
当向钉钉群聊发送消息时，发消息逻辑错误地走到了单聊 (OTO) 的 API 中，导致如下报错：
`chatbotId.notAllow.sendOTO: 错误描述:机器人已经停用或者未启用`
报错日志指向 `nanobot/channels/dingtalk.py` 的 `send` 方法（约 266 行）。
群聊的 chat_id 示例：`cidPb+2XHuDAZ5z2PaF60gbag==`

## 验收标准
1. 检查 `nanobot/channels/dingtalk.py`，定位判断群聊/单聊逻辑的缺陷。
2. 修复路由逻辑：群聊消息应调用正确的群聊 API endpoint（如 `/v1.0/robot/groupMessages/send`），单聊消息继续使用 OTO API。
3. 修复后在此 spec 中记录根因和修复方案。

## 根因分析（RCA）
- 根因 1：`_on_message()` 中对 `conversation_type` 的判断过于严格，使用了 `conversation_type == "2"`。
  - 线上实际值可能是整数 `2`（而不是字符串 `"2"`），导致群聊被误判为单聊。
  - 一旦误判，`metadata.is_group` 会被写成 `False`，`send()` 就会走 OTO 路由。
- 根因 2：`send()` 对路由判断仅依赖 `metadata.is_group`，缺少兜底识别。
  - 当 metadata 缺失/不完整时，即使 `chat_id` 是群会话 ID（例如 `cid...`），也会错误走 OTO。

## 修复方案
- 在 `nanobot/channels/dingtalk.py` 中新增 `_is_group_chat(conversation_type, chat_id)`：
  - 兼容 `conversation_type` 为 `int/float/str/bool`；
  - 支持 `"2"`、`2`、`"group"` 等群聊标识；
  - 当类型字段缺失时，基于 `chat_id` 的 `cid*` 前缀做兜底识别。
- `_on_message()` 改为调用 `_is_group_chat(...)` 计算 `is_group`，避免仅靠 `"2"` 字符串比较。
- `send()` 路由逻辑增强：
  - 优先使用 `metadata.is_group`（若为布尔值）；
  - 否则回退到 `_is_group_chat(metadata.conversation_type, msg.chat_id)` 推断；
  - 群聊稳定走 `/v1.0/robot/groupMessages/send`，单聊继续走 `/v1.0/robot/oToMessages/batchSend`。
- 同时将 `conversation_type` 原始值写入 metadata，便于后续诊断与兼容。

## 验证结果
- 新增测试 `tests/test_dingtalk_channel.py`：
  - `test_on_message_treats_numeric_conversation_type_as_group`
  - `test_send_uses_group_endpoint_when_chat_id_is_group_without_metadata`
- 运行结果：`2 passed`（命令：`uv run --extra dev pytest -q tests/test_dingtalk_channel.py`）。
