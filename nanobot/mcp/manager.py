"""MCP Manager for managing multiple MCP servers."""

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.mcp.client import MCPClient
from nanobot.mcp.tool_wrapper import MCPToolWrapper
from nanobot.agent.tools.registry import ToolRegistry


class MCPManager:
    """
    Manages multiple MCP servers and their tools/resources.

    Responsibilities:
    - Connect to configured MCP servers
    - Wrap MCP tools as nanobot Tools
    - Register tools in the ToolRegistry
    - Provide resource access
    """

    def __init__(self, tool_registry: ToolRegistry):
        """
        Initialize MCP manager.

        Args:
            tool_registry: The tool registry to register MCP tools.
        """
        self.tool_registry = tool_registry
        self._clients: dict[str, MCPClient] = {}
        self._initialized = False

    async def add_server(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None
    ) -> bool:
        """
        Add and connect to an MCP server.

        Args:
            name: Server name.
            command: Command to start the server.
            args: Command arguments.
            env: Environment variables.

        Returns:
            True if connected successfully.
        """
        if name in self._clients:
            logger.warning(f"MCP server '{name}' already added")
            return False

        client = MCPClient(name, command, args, env)

        if not await client.connect():
            logger.error(f"Failed to connect to MCP server '{name}'")
            return False

        self._clients[name] = client

        # Register tools from this server
        await self._register_server_tools(client)

        logger.info(f"MCP server '{name}' added successfully")
        return True

    async def remove_server(self, name: str) -> bool:
        """
        Remove and disconnect from an MCP server.

        Args:
            name: Server name.

        Returns:
            True if removed successfully.
        """
        client = self._clients.get(name)
        if not client:
            logger.warning(f"MCP server '{name}' not found")
            return False

        # Unregister tools
        await self._unregister_server_tools(client)

        # Disconnect
        await client.disconnect()

        del self._clients[name]
        logger.info(f"MCP server '{name}' removed")
        return True

    async def _register_server_tools(self, client: MCPClient) -> None:
        """Register all tools from an MCP server."""
        for tool_def in client.tools:
            tool_name = f"mcp_{client.name}_{tool_def['name']}"
            wrapper = MCPToolWrapper(
                client=client,
                tool_name=tool_def["name"],
                tool_description=tool_def.get("description", ""),
                tool_schema=tool_def.get("inputSchema", {}),
                name_prefix=f"mcp_{client.name}_"
            )
            self.tool_registry.register(wrapper)
            logger.debug(f"Registered MCP tool: {tool_name}")

    async def _unregister_server_tools(self, client: MCPClient) -> None:
        """Unregister all tools from an MCP server."""
        for tool_def in client.tools:
            tool_name = f"mcp_{client.name}_{tool_def['name']}"
            self.tool_registry.unregister(tool_name)
            logger.debug(f"Unregistered MCP tool: {tool_name}")

    async def initialize_from_config(self, mcp_config: dict[str, Any]) -> None:
        """
        Initialize MCP servers from configuration.

        Expected config format:
        {
            "servers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/root"],
                    "env": {}
                }
            }
        }

        Args:
            mcp_config: MCP configuration dict.
        """
        servers = mcp_config.get("servers", {})

        for name, config in servers.items():
            command = config.get("command")
            args = config.get("args", [])
            env = config.get("env", {})

            if not command:
                logger.warning(f"MCP server '{name}' missing command, skipping")
                continue

            await self.add_server(name, command, args, env)

        self._initialized = True

    async def shutdown(self) -> None:
        """Shutdown all MCP servers."""
        for name in list(self._clients.keys()):
            await self.remove_server(name)
        self._initialized = False

    def get_client(self, name: str) -> MCPClient | None:
        """
        Get an MCP client by name.

        Args:
            name: Server name.

        Returns:
            MCPClient if found, None otherwise.
        """
        return self._clients.get(name)

    def list_servers(self) -> list[dict[str, Any]]:
        """
        List all connected MCP servers.

        Returns:
            List of server info dicts.
        """
        return [
            {
                "name": name,
                "connected": client.is_connected,
                "tools": len(client.tools),
                "resources": len(client.resources),
                "prompts": len(client.prompts)
            }
            for name, client in self._clients.items()
        ]

    async def read_resource(self, server_name: str, uri: str) -> str:
        """
        Read a resource from an MCP server.

        Args:
            server_name: Server name.
            uri: Resource URI.

        Returns:
            Resource content as string.
        """
        client = self.get_client(server_name)
        if not client:
            raise ValueError(f"MCP server '{server_name}' not found")

        contents = await client.read_resource(uri)

        # Combine all content parts
        result_parts = []
        for content in contents:
            if content.get("type") == "text":
                result_parts.append(content.get("text", ""))
            elif content.get("type") == "blob":
                # Base64 encoded data
                result_parts.append(f"[Binary data: {content.get('mimeType', 'unknown')}]")

        return "\n".join(result_parts)

    @property
    def is_initialized(self) -> bool:
        """Check if manager is initialized."""
        return self._initialized

    @property
    def server_count(self) -> int:
        """Get number of connected servers."""
        return len(self._clients)
