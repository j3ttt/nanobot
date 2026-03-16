# Session Archive — 全量对话归档

## 背景
当前 memory_worker 在 consolidation 时只保留最近 6 条消息，旧消息被 LLM 摘要后原文丢弃。
需要一个 append-only 的归档机制，保留所有原始对话记录，用于未来蒸馏训练数据集。

## 需求
1. 每条新消息写入 session 的同时，追加写入一个归档文件
2. 归档文件永不截断、永不覆盖，只追加
3. 归档路径：`~/.nanobot/workspace/archive/{session_key}.jsonl`
4. 每行一条消息，JSON 格式，保留所有字段（role, content, timestamp, tool_calls 等）
5. 不影响现有 session 管理和 consolidation 逻辑
6. 性能：异步写入或同步追加均可，不阻塞主流程

## 实现方案
在 `Session.add_message()` 中加入归档逻辑，或在 `SessionManager.save()` 中增量追加新消息。

推荐方案：在 `SessionManager` 中新增 `_archive_message()` 方法，在 `save()` 调用时对比已归档数量，追加新增消息。

### 关键设计
- `SessionManager` 初始化时创建 `archive_dir`
- 每个 session 对应一个归档文件 `archive/{safe_key}.jsonl`
- 用一个内存计数器 `_archived_count[key]` 记录已归档消息数，save 时只追加 `messages[archived_count:]`
- 启动时从归档文件行数恢复计数器（或直接每次追加，用消息 timestamp 去重）

## 验收标准
- [ ] 新消息在 save 时自动追加到归档文件
- [ ] consolidation 截断 session 后，归档文件不受影响
- [ ] 重启后归档继续追加，不重复
- [ ] 归档文件格式：每行一条 JSON，包含 role/content/timestamp 等全部字段
