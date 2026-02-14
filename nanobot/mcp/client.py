"""MCP Client for connecting to MCP servers."""

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger


class MCPClient:
    """
    Client for connecting to an MCP server.

    Supports stdio transport (subprocess-based communication).
    Future: SSE transport for HTTP-based MCP servers.
    """

    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        """
        Initialize MCP client.

        Args:
            name: Server name for identification.
            command: Command to start the MCP server.
            args: Command arguments.
            env: Environment variables for the server process.
        """
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._message_id = 0
        self._initialized = False

        # Cache for server capabilities
        self._tools: list[dict[str, Any]] = []
        self._resources: list[dict[str, Any]] = []
        self._prompts: list[dict[str, Any]] = []

    async def connect(self) -> bool:
        """
        Connect to the MCP server and initialize.

        Returns:
            True if connected successfully.
        """
        try:
            import os

            # Prepare environment
            proc_env = os.environ.copy()
            proc_env.update(self.env)

            # Start the server process
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env
            )

            logger.info(f"MCP server '{self.name}' started: {self.command} {' '.join(self.args)}")

            # Initialize the connection
            init_response = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": False},
                    "sampling": {}
                },
                "clientInfo": {
                    "name": "nanobot",
                    "version": "0.1.3"
                }
            })

            if not init_response:
                logger.error(f"MCP server '{self.name}' initialization failed")
                return False

            # Send initialized notification
            await self._send_notification("notifications/initialized")

            self._initialized = True

            # Discover capabilities
            await self._discover_capabilities()

            logger.info(f"MCP server '{self.name}' initialized: {len(self._tools)} tools, {len(self._resources)} resources")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to MCP server '{self.name}': {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception as e:
                logger.warning(f"Error disconnecting from MCP server '{self.name}': {e}")
            finally:
                self._process = None
                self._initialized = False

    async def _discover_capabilities(self) -> None:
        """Discover tools, resources, and prompts from the server."""
        # List tools
        tools_response = await self._send_request("tools/list", {})
        if tools_response and "tools" in tools_response:
            self._tools = tools_response["tools"]

        # List resources
        resources_response = await self._send_request("resources/list", {})
        if resources_response and "resources" in resources_response:
            self._resources = resources_response["resources"]

        # List prompts
        prompts_response = await self._send_request("prompts/list", {})
        if prompts_response and "prompts" in prompts_response:
            self._prompts = prompts_response["prompts"]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.

        Returns:
            Tool result.
        """
        if not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not initialized")

        response = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        if not response:
            raise RuntimeError(f"Tool '{tool_name}' call failed")

        return response.get("content", [])

    async def read_resource(self, uri: str) -> Any:
        """
        Read a resource from the MCP server.

        Args:
            uri: Resource URI.

        Returns:
            Resource content.
        """
        if not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not initialized")

        response = await self._send_request("resources/read", {"uri": uri})

        if not response:
            raise RuntimeError(f"Resource '{uri}' read failed")

        return response.get("contents", [])

    async def get_prompt(self, prompt_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """
        Get a prompt from the MCP server.

        Args:
            prompt_name: Name of the prompt.
            arguments: Prompt arguments.

        Returns:
            Prompt content.
        """
        if not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not initialized")

        response = await self._send_request("prompts/get", {
            "name": prompt_name,
            "arguments": arguments or {}
        })

        if not response:
            raise RuntimeError(f"Prompt '{prompt_name}' get failed")

        return response.get("messages", [])

    async def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC request and wait for response."""
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None

        self._message_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._message_id,
            "method": method,
            "params": params
        }

        try:
            # Send request
            request_data = json.dumps(request) + "\n"
            self._process.stdin.write(request_data.encode())
            await self._process.stdin.drain()

            # Read response
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=30.0
            )

            if not response_line:
                return None

            response = json.loads(response_line.decode())

            if "error" in response:
                logger.error(f"MCP server '{self.name}' error: {response['error']}")
                return None

            return response.get("result")

        except asyncio.TimeoutError:
            logger.error(f"MCP server '{self.name}' request timeout: {method}")
            return None
        except Exception as e:
            logger.error(f"MCP server '{self.name}' request failed: {e}")
            return None

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }

        try:
            notification_data = json.dumps(notification) + "\n"
            self._process.stdin.write(notification_data.encode())
            await self._process.stdin.drain()
        except Exception as e:
            logger.error(f"MCP server '{self.name}' notification failed: {e}")

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Get list of available tools."""
        return self._tools

    @property
    def resources(self) -> list[dict[str, Any]]:
        """Get list of available resources."""
        return self._resources

    @property
    def prompts(self) -> list[dict[str, Any]]:
        """Get list of available prompts."""
        return self._prompts

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server."""
        return self._initialized and self._process is not None
