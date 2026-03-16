# DingTalk Local Image Send Fix

## Context
Currently, when sending a local image path like `![meme](/Users/jettt/.nanobot/workspace/memes/xxx.gif)` or `[图片](/Users/jettt/.nanobot/workspace/memes/xxx.gif)` over the DingTalk channel, the DingTalk app fails to display it, showing an "不支持的URL" error. 
The system was supposed to automatically detect local image paths, upload them to DingTalk to obtain a `media_id`, and send them as an image message.

## Objective
Fix the DingTalk channel implementation in nanobot so that it correctly intercepts local image paths in messages, uploads them via DingTalk's media upload API, and sends them as proper image messages.

## Acceptance Criteria
1. When the bot outputs a markdown image tag with an absolute local path (e.g., `![alt](/path/to/image.gif)`), the DingTalk channel should parse it, upload the file, and send it as a native DingTalk image/media message.
2. If the message contains both text and an image, they should both be sent (either as separate messages or a combined rich message, depending on DingTalk API capabilities).
3. The fix must handle errors gracefully (e.g., file not found, upload failure) and fall back to sending the text.
4. All tests must pass.

## Steps
1. Analyze `nanobot/channels/dingtalk.py` or the relevant DingTalk message sending logic.
2. Implement the local path detection and media upload logic.
3. Update the message sending payload to use the obtained `media_id`.
4. Verify the fix by running tests.
