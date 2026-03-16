# Spec: Fix DingTalk Local Image Send

## Context
Currently, the DingTalk channel only sends an image if `msg.metadata.get("msgtype") == "image"`. 
However, the LLM typically outputs markdown text containing local image paths, like `![alt](/path/to/image.gif)`. 
When this happens, the channel sends it as a `sampleMarkdown` message, which DingTalk rejects because it doesn't support local file paths in markdown images.

## Requirements
Modify `nanobot/channels/dingtalk.py` to automatically detect local image paths in outgoing markdown messages and send them correctly.

1. **Markdown Parsing**:
   - When sending a message (in `send()`), check if `msg.content` contains markdown image syntax with a local absolute path (e.g., `![alt](/Users/.../image.gif)`).
   - Extract all such local paths.

2. **Image Upload and Sending**:
   - For each extracted local path, verify if the file exists.
   - If it exists, upload it using `_upload_local_image` to get a `media_id`.
   - Send each uploaded image as a separate `sampleImageMsg` to the same chat.
   
3. **Text Cleanup**:
   - Remove the markdown image tags (`![alt](/path/...)`) from the original `msg.content`.
   - If there is any remaining text (after stripping whitespace), send it as a `sampleMarkdown` message.
   - If the content was *only* the image tag, do not send an empty text message.

4. **Backward Compatibility**:
   - Keep the existing `msgtype == "image"` logic intact.

## Acceptance Criteria
- When the LLM replies with `Here is the image: ![meme](/Users/jettt/.nanobot/workspace/memes/叹气.gif)`, the user receives the text "Here is the image:" and a separate image message containing the meme.
- No `msgtype` metadata is required from the LLM.
- Works for both group chats and private chats.

