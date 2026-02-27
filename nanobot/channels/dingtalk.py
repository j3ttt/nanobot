"""DingTalk/DingDing channel implementation using Stream Mode."""

import asyncio
import base64
import json
import time
from typing import Any

from loguru import logger
import httpx

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import DingTalkConfig

try:
    from dingtalk_stream import (
        DingTalkStreamClient,
        Credential,
        CallbackHandler,
        CallbackMessage,
        AckMessage,
    )
    from dingtalk_stream.chatbot import ChatbotMessage

    DINGTALK_AVAILABLE = True
except ImportError:
    DINGTALK_AVAILABLE = False
    # Fallback so class definitions don't crash at module level
    CallbackHandler = object  # type: ignore[assignment,misc]
    CallbackMessage = None  # type: ignore[assignment,misc]
    AckMessage = None  # type: ignore[assignment,misc]
    ChatbotMessage = None  # type: ignore[assignment,misc]


class NanobotDingTalkHandler(CallbackHandler):
    """
    Standard DingTalk Stream SDK Callback Handler.
    Parses incoming messages and forwards them to the Nanobot channel.
    """

    def __init__(self, channel: "DingTalkChannel"):
        super().__init__()
        self.channel = channel

    async def process(self, message: CallbackMessage):
        """Process incoming stream message."""
        try:
            # Parse using SDK's ChatbotMessage for robust handling
            chatbot_msg = ChatbotMessage.from_dict(message.data)
            logger.debug(
                "DingTalk raw inbound message: msgtype={}, msgId={}",
                message.data.get("msgtype"),
                message.data.get("msgId"),
            )

            # Extract text content; fall back to raw dict if SDK object is empty
            content = ""
            if chatbot_msg.text:
                content = chatbot_msg.text.content.strip()
            if not content:
                content = message.data.get("text", {}).get("content", "").strip()

            image_paths = await self.channel._download_incoming_images(chatbot_msg, message.data)
            if image_paths:
                logger.debug(
                    "Prepared {} DingTalk image(s) for msgId={}",
                    len(image_paths),
                    chatbot_msg.message_id,
                )
                if content:
                    content = f"{content}\n" + "\n".join("[image]" for _ in image_paths)
                else:
                    content = "\n".join("[image]" for _ in image_paths)

            if not content:
                logger.warning(
                    f"Received empty or unsupported DingTalk message type: {chatbot_msg.message_type}"
                )
                return AckMessage.STATUS_OK, "OK"

            sender_id = chatbot_msg.sender_staff_id or chatbot_msg.sender_id
            sender_name = chatbot_msg.sender_nick or "Unknown"
            conversation_id = chatbot_msg.conversation_id
            conversation_type = chatbot_msg.conversation_type  # "1" = private, "2" = group

            logger.info(f"Received DingTalk message from {sender_name} ({sender_id}): {content}")

            # Forward to Nanobot via _on_message (non-blocking).
            # Store reference to prevent GC before task completes.
            task = asyncio.create_task(
                self.channel._on_message(
                    content,
                    sender_id,
                    sender_name,
                    conversation_id,
                    conversation_type,
                    media=image_paths,
                    message_type=chatbot_msg.message_type,
                )
            )
            self.channel._background_tasks.add(task)
            task.add_done_callback(self.channel._background_tasks.discard)

            return AckMessage.STATUS_OK, "OK"

        except Exception as e:
            logger.error(f"Error processing DingTalk message: {e}")
            # Return OK to avoid retry loop from DingTalk server
            return AckMessage.STATUS_OK, "Error"


class DingTalkChannel(BaseChannel):
    """
    DingTalk channel using Stream Mode.

    Uses WebSocket to receive events via `dingtalk-stream` SDK.
    Uses direct HTTP API to send messages (SDK is mainly for receiving).

    Note: Currently only supports private (1:1) chat. Group messages are
    received but replies are sent back as private messages to the sender.
    """

    name = "dingtalk"

    def __init__(self, config: DingTalkConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: DingTalkConfig = config
        self._client: Any = None
        self._http: httpx.AsyncClient | None = None

        # Access Token management for sending messages
        self._access_token: str | None = None
        self._token_expiry: float = 0

        # Hold references to background tasks to prevent GC
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Start the DingTalk bot with Stream Mode."""
        try:
            if not DINGTALK_AVAILABLE:
                logger.error("DingTalk Stream SDK not installed. Run: pip install dingtalk-stream")
                return

            if not self.config.client_id or not self.config.client_secret:
                logger.error("DingTalk client_id and client_secret not configured")
                return

            self._running = True
            self._http = httpx.AsyncClient()

            logger.info(
                f"Initializing DingTalk Stream Client with Client ID: {self.config.client_id}..."
            )
            credential = Credential(self.config.client_id, self.config.client_secret)
            self._client = DingTalkStreamClient(credential)

            # Register standard handler
            handler = NanobotDingTalkHandler(self)
            self._client.register_callback_handler(ChatbotMessage.TOPIC, handler)

            logger.info("DingTalk bot started with Stream Mode")

            # Reconnect loop: restart stream if SDK exits or crashes
            while self._running:
                try:
                    await self._client.start()
                except Exception as e:
                    logger.warning(f"DingTalk stream error: {e}")
                if self._running:
                    logger.info("Reconnecting DingTalk stream in 5 seconds...")
                    await asyncio.sleep(5)

        except Exception as e:
            logger.exception(f"Failed to start DingTalk channel: {e}")

    async def stop(self) -> None:
        """Stop the DingTalk bot."""
        self._running = False
        # Close the shared HTTP client
        if self._http:
            await self._http.aclose()
            self._http = None
        # Cancel outstanding background tasks
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

    async def _get_access_token(self) -> str | None:
        """Get or refresh Access Token."""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        data = {
            "appKey": self.config.client_id,
            "appSecret": self.config.client_secret,
        }

        if not self._http:
            logger.warning("DingTalk HTTP client not initialized, cannot refresh token")
            return None

        try:
            resp = await self._http.post(url, json=data)
            resp.raise_for_status()
            res_data = resp.json()
            self._access_token = res_data.get("accessToken")
            # Expire 60s early to be safe
            self._token_expiry = time.time() + int(res_data.get("expireIn", 7200)) - 60
            return self._access_token
        except Exception as e:
            logger.error(f"Failed to get DingTalk access token: {e}")
            return None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through DingTalk."""
        token = await self._get_access_token()
        if not token:
            return

        if not self._http:
            logger.warning("DingTalk HTTP client not initialized, cannot send")
            return

        headers = {"x-acs-dingtalk-access-token": token}
        is_group = msg.metadata.get("is_group", False)

        if is_group:
            # Group chat: use groupMessages/send with openConversationId
            # https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message

            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            data = {
                "robotCode": self.config.client_id,
                "openConversationId": msg.chat_id,
                "msgKey": "sampleMarkdown",
                "msgParam": json.dumps(
                    {
                        "text": msg.content,
                        "title": "Nanobot Reply",
                    }
                ),
            }
        else:
            # Private chat: use oToMessages/batchSend with userIds
            # https://open.dingtalk.com/document/orgapp/robot-batch-send-messages
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            data = {
                "robotCode": self.config.client_id,
                "userIds": [msg.chat_id],
                "msgKey": "sampleMarkdown",
                "msgParam": json.dumps(
                    {
                        "text": msg.content,
                        "title": "Nanobot Reply",
                    }
                ),
            }

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            if resp.status_code != 200:
                logger.error(f"DingTalk send failed: {resp.text}")
            else:
                logger.debug(f"DingTalk message sent to {msg.chat_id} (is_group={is_group})")
        except Exception as e:
            logger.error(f"Error sending DingTalk message: {e}")

    def _extract_image_download_codes(
        self, chatbot_msg: Any, raw_data: dict[str, Any]
    ) -> list[str]:
        """Extract image downloadCode from SDK object and raw message."""
        codes: list[str] = []

        msgtype = getattr(chatbot_msg, "message_type", None) or raw_data.get("msgtype")
        image_content = getattr(chatbot_msg, "image_content", None)
        if msgtype == "picture" and image_content and getattr(image_content, "download_code", None):
            codes.append(image_content.download_code)

        rich_text_content = getattr(chatbot_msg, "rich_text_content", None)
        if msgtype == "richText" and rich_text_content and rich_text_content.rich_text_list:
            for item in rich_text_content.rich_text_list:
                code = item.get("downloadCode")
                if code:
                    codes.append(code)

        # Backward-compatible raw dict fallback; do not rely on SDK helper methods.
        raw_content = raw_data.get("content", {})
        if isinstance(raw_content, dict):
            raw_code = raw_content.get("downloadCode")
            if raw_code:
                codes.append(raw_code)

            for item in raw_content.get("richText", []) or []:
                if isinstance(item, dict) and item.get("downloadCode"):
                    codes.append(item["downloadCode"])

        deduped = list(dict.fromkeys(codes))
        logger.debug(
            "Extracted {} DingTalk image downloadCode(s) for msgId={}: {}",
            len(deduped),
            raw_data.get("msgId"),
            deduped,
        )
        return deduped

    async def _get_image_download_url(self, download_code: str) -> str | None:
        """Fetch temporary download URL for DingTalk image by downloadCode."""
        token = await self._get_access_token()
        if not token:
            logger.warning("Cannot get DingTalk image download URL: missing access token")
            return None
        if not self._http:
            logger.warning("Cannot get DingTalk image download URL: HTTP client not initialized")
            return None

        url = "https://api.dingtalk.com/v1.0/robot/messageFiles/download"
        headers = {"x-acs-dingtalk-access-token": token}
        data = {"robotCode": self.config.client_id, "downloadCode": download_code}

        try:
            resp = await self._http.post(url, json=data, headers=headers)
            resp.raise_for_status()
            download_url = resp.json().get("downloadUrl")
            if not download_url:
                logger.warning("DingTalk messageFiles/download response has no downloadUrl")
                return None
            logger.debug("Resolved DingTalk downloadCode={} to URL", download_code)
            return download_url
        except Exception as e:
            logger.warning(
                "Failed to resolve DingTalk image downloadCode={}: {}",
                download_code,
                e,
            )
            return None

    def _guess_image_mime(self, content: bytes) -> str | None:
        """Guess image MIME type from magic bytes."""
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return None

    async def _download_image_data_url(self, download_url: str, download_code: str) -> str | None:
        """Download DingTalk image and return it as data URL for multimodal LLM input."""
        if not self._http:
            logger.warning("Cannot download DingTalk image: HTTP client not initialized")
            return None

        try:
            resp = await self._http.get(download_url)
            resp.raise_for_status()
            content = resp.content
            mime = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if not mime.startswith("image/"):
                mime = self._guess_image_mime(content) or ""
            if not mime.startswith("image/"):
                logger.warning(
                    "Skip DingTalk image {}: unsupported content-type '{}'",
                    download_code,
                    resp.headers.get("content-type"),
                )
                return None

            b64 = base64.b64encode(content).decode("ascii")
            data_url = f"data:{mime};base64,{b64}"
            logger.debug(
                "Converted DingTalk image downloadCode={} to data URL ({} bytes, mime={})",
                download_code,
                len(content),
                mime,
            )
            return data_url
        except Exception as e:
            logger.warning("Failed to download DingTalk image {}: {}", download_code, e)
            return None

    async def _download_incoming_images(
        self, chatbot_msg: Any, raw_data: dict[str, Any]
    ) -> list[str]:
        """Download all images from an incoming DingTalk message."""
        download_codes = self._extract_image_download_codes(chatbot_msg, raw_data)
        if not download_codes:
            return []

        media_paths: list[str] = []
        for download_code in download_codes:
            download_url = await self._get_image_download_url(download_code)
            if not download_url:
                continue
            image_data_url = await self._download_image_data_url(download_url, download_code)
            if image_data_url:
                media_paths.append(image_data_url)
        return media_paths

    async def _on_message(
        self,
        content: str,
        sender_id: str,
        sender_name: str,
        conversation_id: str,
        conversation_type: str,
        media: list[str] | None = None,
        message_type: str | None = None,
    ) -> None:
        """Handle incoming message (called by NanobotDingTalkHandler).

        Delegates to BaseChannel._handle_message() which enforces allow_from
        permission checks before publishing to the bus.
        """
        try:
            # conversation_type: "1" = private chat, "2" = group chat
            is_group = conversation_type == "2"

            logger.info(f"DingTalk inbound: {content} from {sender_name} (is_group={is_group})")
            await self._handle_message(
                sender_id=conversation_id if is_group else sender_id,
                chat_id=conversation_id if is_group else sender_id,
                content=str(content),
                media=media or [],
                metadata={
                    "sender_name": sender_name,
                    "platform": "dingtalk",
                    "is_group": is_group,
                    "conversation_id": conversation_id,
                    "message_type": message_type,
                },
            )
        except Exception as e:
            logger.error(f"Error publishing DingTalk message: {e}")
