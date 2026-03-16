# 钉钉本地图片发送与表情包检索 (DingTalk Local Meme Image Send)

## 背景
用户希望机器人在聊天时能够根据语境发送本地的表情包（meme）图片。目前 DingTalk channel 的 `send` 逻辑仅支持文本/Markdown。我们需要扩展能力，使其能够读取本地图片文件，上传至钉钉获取 `media_id`，并发送到对应的单聊/群聊中。

## 验收标准
1. **本地图片上传**：在 `nanobot/channels/dingtalk.py` 中增加调用钉钉媒体上传接口的逻辑，输入本地路径，返回 `media_id`。
2. **扩展发信逻辑**：更新 `send` 方法，当 `metadata.get('msgtype') == 'image'` 且提供本地文件路径时，自动完成“上传 -> 获取 media_id -> 发送图片消息”的完整链路。需同时兼容单聊和群聊。
3. **检索技能 (Skill)**：在 `nanobot/skills/` 下新建一个 `meme` 技能。该技能提供一个通过关键词匹配 `/Users/jettt/.nanobot/workspace/memes/` 目录下文件名的工具方法（例如 `find_meme(keyword)` 返回最匹配的图片绝对路径）。
4. **测试**：在 `tests/` 下补充相关测试用例（可 mock 上传接口和检索逻辑）。

## 上下文
- `dingtalk.py` 已经修复了单聊/群聊的路由问题。
- 用户会将表情包以类似 `[嘲讽].jpg`、`叹气.gif` 的命名存放在 `/Users/jettt/.nanobot/workspace/memes/`。
