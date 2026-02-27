"""Wrapper to adapt MCP tools to nanobot Tool interface."""

import json
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.mcp.client import MCPClient


class MCPToolWrapper(Tool):
    """
    Wraps an MCP tool as a nanobot Tool.

    Translates between nanobot's tool interface and MCP's protocol.
    """

    def __init__(
        self,
        client: MCPClient,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        name_prefix: str = "mcp_"
    ):
        """
        Initialize MCP tool wrapper.

        Args:
            client: MCP client for the server providing this tool.
            tool_name: Original MCP tool name.
            tool_description: Tool description.
            tool_schema: JSON Schema for tool parameters.
            name_prefix: Prefix for the tool name in nanobot.
        """
        self.client = client
        self.tool_name = tool_name
        self.tool_description = tool_description
        self.tool_schema = tool_schema
        self.name_prefix = name_prefix

    @property
    def name(self) -> str:
        """Tool name used in function calls."""
        return f"{self.name_prefix}{self.tool_name}"

    @property
    def description(self) -> str:
        """Description of what the tool does."""
        server_name = self.client.name
        return f"[MCP:{server_name}] {self.tool_description}"

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        # MCP uses inputSchema directly, which is already JSON Schema
        return self.tool_schema

    async def execute(self, **kwargs: Any) -> str:
        """
        Execute the MCP tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters.

        Returns:
            String result of the tool execution.
        """
        try:
            # Call the MCP tool
            result = await self.client.call_tool(self.tool_name, kwargs)

            # Format the result
            return self._format_result(result)

        except Exception as e:
            logger.error(f"MCP tool '{self.name}' execution failed: {e}")
            return f"Error executing MCP tool '{self.tool_name}': {str(e)}"

    def _format_result(self, result: list[dict[str, Any]]) -> str:
        """
        Format MCP tool result into a string.

        MCP tools can return multiple content items (text, images, resources).
        We format them into a readable string for the agent.

        Args:
            result: List of content items from MCP.

        Returns:
            Formatted result string.
        """
        if not result:
            return "Tool executed successfully (no output)"

        parts = []

        for item in result:
            content_type = item.get("type")

            if content_type == "text":
                text = item.get("text", "")
                parts.append(text)

            elif content_type == "image":
                # Image data (base64 encoded)
                mime_type = item.get("mimeType", "image")
                data = item.get("data", "")
                parts.append(f"[Image: {mime_type}, {len(data)} bytes]")

            elif content_type == "resource":
                # Resource reference
                uri = item.get("uri", "")
                mime_type = item.get("mimeType", "")
                parts.append(f"[Resource: {uri} ({mime_type})]")

            else:
                # Unknown content type, try JSON serialization
                try:
                    parts.append(json.dumps(item, indent=2))
                except Exception:
                    parts.append(str(item))

        return "\n\n".join(parts) if parts else "Tool executed successfully"
