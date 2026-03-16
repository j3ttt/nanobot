# Spec: Intermediate Message Push (中间消息推送)

## 日期
2026-03-12

## 问题
当 LLM 返回文本 + tool_calls 时，文本部分（如"我去查一下源码"）只存入 messages 历史给下一轮 LLM 看，从未推送给用户。用户在 tool 执行期间（可能几十秒）看到的是一片空白，不知道 agent 是卡住了还是在工作。

## 目标
在 agent loop 中，当 LLM 返回同时包含 content 和 tool_calls 时，先将 content 部分立即推送给用户，再执行 tool calls。让用户能看到中间状态消息。

## 改动范围
仅 `nanobot/agent/loop.py`

## 方案

### 核心改动
在 `_process_message` 方法的 agent loop 中，`if response.has_tool_calls:` 分支内，执行 tool 之前，检查 `response.content` 是否非空，如果是则通过 `bus.publish_outbound` 推送给用户。

同样的改动也需要应用到 `_process_system_message` 方法中的对应分支。

### 伪代码
```python
if response.has_tool_calls:
    # 新增：推送中间消息
    if response.content and response.content.strip():
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=response.content,
            metadata=msg.metadata or {},
        ))

    # 原有逻辑不变：add_assistant_message, execute tools...
```

### 注意事项
1. `bus.publish_outbound` 是 async queue push，channel 消费端异步发送，不阻塞 tool 执行
2. session 保存只记录 final_content（最后一轮无 tool_calls 的回复），中间推送的文本不重复存 session history（它已经在 messages 的 assistant message 里了，通过 add_assistant_message）
3. 不需要改 bus 层或 channel 层，完全利用现有基础设施
4. metadata 透传确保 channel 特定信息（如钉钉的 webhook 地址）不丢失

## 验收标准
1. 当 LLM 返回 content + tool_calls 时，用户立即收到 content 部分的消息
2. tool 执行不受影响，最终回复正常发送
3. 中间消息不重复存入 session history
4. 空白 content（空字符串或纯空格）不触发推送
5. `_process_message` 和 `_process_system_message` 两个方法都需要改

## 状态
- [x] 实现 (2026-03-12, loop.py L256 + L395)
- [ ] 测试（重启后验证）
- [ ] 部署验证
