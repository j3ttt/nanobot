# 标题：Telegram sendMessageDraft 流式传输接入

## 1. 背景与问题

- 当前 Telegram 通道仅在 LLM 完成后一次性 `send_message` 回复。
- 用户体验上缺少“正在生成”的可见进度，尤其长回复会有等待空窗。
- Telegram 新增 `sendMessageDraft`，可用于发送部分内容草稿，改善等待体验。

## 2. 目标与非目标

### 目标

- 在不破坏现有消息发送链路的前提下，接入 `sendMessageDraft`。
- 在最终正式消息发送前，发送若干次递进草稿文本。
- 任意草稿调用失败时自动回退，不影响最终消息发送。

### 非目标

- 不改造 LLM 侧为 token-by-token 真流式生成。
- 不改动其他渠道（Discord/Slack/Feishu 等）的发送逻辑。
- 不引入复杂的 draft 生命周期管理（如撤稿、编辑历史持久化）。

## 3. 用户场景

- 场景 1：用户在 Telegram 私聊中发起复杂问题，机器人先显示递进草稿，再落地最终消息。
- 场景 2：草稿接口不可用或网络异常，机器人仍通过原 `send_message` 正常回复。

## 4. 设计方案

- 文件：`nanobot/channels/telegram.py`
- 在 `send()` 中保留原有 `send_message(parse_mode="HTML")` 作为最终消息发送路径。
- 在最终发送前新增 `_maybe_stream_draft()`：
  - 仅在私聊尝试（群聊跳过，按 Bot API 文档约束）。
  - 文本长度超过 4096 时跳过。
  - 将完整文本切分为少量阶段（默认 6 步）递进发送。
- 新增 `_send_message_draft()` 直接调用：
  - `POST https://api.telegram.org/bot<TOKEN>/sendMessageDraft`
  - 参数：`chat_id`, `draft_id`, `text`, `parse_mode`（可选 `message_thread_id`）
- 失败处理：
  - `sendMessageDraft` 任一步失败则停止草稿流，继续最终 `send_message`。

## 5. 实施计划

1. 在 Telegram channel 增加 draft 功能方法与开关。
2. 将 draft 流接入 `send()` 的最终发送前阶段。
3. 增加异常回退日志，确保不影响原链路。
4. 语法检查并手工验证私聊与群聊行为。

## 6. 风险与回滚

- 风险：
  - Telegram 草稿接口行为变更导致异常返回。
  - 高频草稿调用触发限流。
- 缓解：
  - 限制更新步数和间隔（小步数、短延迟）。
  - 任意异常立即回退到原发送方式。
- 回滚策略：
  - 移除 `send()` 中 `_maybe_stream_draft()` 调用即可恢复旧行为。

## 7. 验收标准

- [ ] 私聊消息在最终回复前出现递进草稿效果。
- [ ] 群聊不调用草稿接口，直接走原发送逻辑。
- [ ] 草稿接口失败时，最终消息仍能正常送达。
- [ ] 原有 markdown->HTML 渲染能力不回归。

## 8. 测试计划

- 单元测试：
  - 暂无（当前仓库未为 Telegram channel 建立系统化 mock 测试）。
- 集成测试：
  - 在真实 Telegram bot 环境下私聊触发长回复，观察 draft + final 行为。
- 手工验证：
  - 私聊：正常 draft + final。
  - 群聊：无 draft，仅 final。
  - 人工制造 draft 失败（错误 token/网络）验证回退。

## 9. 进度记录

- 2026-03-03：完成设计与实现，接入 `sendMessageDraft`，并确认群聊路径不启用 draft。

