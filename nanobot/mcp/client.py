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

        # CRITICAL-1 FIX: Request/response correlation
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None

        # CRITICAL-4 FIX: stderr monitoring
        self._stderr_task: asyncio.Task | None = None

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

            # CRITICAL-1 FIX: Start background read loop
            self._read_task = asyncio.create_task(self._read_loop())

            # CRITICAL-4 FIX: Start stderr monitor
            self._stderr_task = asyncio.create_task(self._monitor_stderr())

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
                # CRITICAL-2 FIX: Clean up on failure
                await self.disconnect()
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
            # CRITICAL-2 FIX: Clean up on any exception
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        # CRITICAL-1 FIX: Cancel read loop
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        # CRITICAL-4 FIX: Cancel stderr monitor
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        # CRITICAL-1 FIX: Clean up pending requests
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        if self._process:
            try:
                # Check if already terminated
                if self._process.returncode is None:
                    self._process.terminate()
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                if self._process.returncode is None:
                    self._process.kill()
                    await self._process.wait()
            except Exception as e:
                logger.warning(f"Error disconnecting from MCP server '{self.name}': {e}")
            finally:
                self._process = None
                self._initialized = False

    async def _read_loop(self) -> None:
        """
        Background task to read and correlate responses.

        CRITICAL-1 FIX: This ensures responses are correctly matched to requests
        even when multiple concurrent requests are in flight.
        """
        if not self._process or not self._process.stdout:
            return

        try:
            while self._process and self._process.stdout:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    response = json.loads(line.decode())
                    msg_id = response.get("id")

                    if msg_id is not None and msg_id in self._pending_requests:
                        future = self._pending_requests.pop(msg_id)
                        if not future.done():
                            if "error" in response:
                                future.set_exception(
                                    RuntimeError(f"MCP error: {response['error']}")
                                )
                            else:
                                future.set_result(response.get("result"))
                    else:
                        # Server-initiated notification or unmatched response
                        logger.debug(f"MCP[{self.name}] unmatched message: {response}")

                except json.JSONDecodeError as e:
                    logger.error(f"MCP[{self.name}] invalid JSON response: {e}")
                except Exception as e:
                    logger.error(f"MCP[{self.name}] read loop error processing message: {e}")

        except asyncio.CancelledError:
            # Normal shutdown
            pass
        except Exception as e:
            logger.error(f"MCP[{self.name}] read loop error: {e}")
        finally:
            # Cancel all pending requests on read loop exit
            for msg_id, future in list(self._pending_requests.items()):
                if not future.done():
                    future.set_exception(
                        RuntimeError(f"MCP server '{self.name}' read loop terminated")
                    )
            self._pending_requests.clear()

    async def _monitor_stderr(self) -> None:
        """
        Monitor stderr to prevent pipe blocking.

        CRITICAL-4 FIX: Without consuming stderr, the MCP server process can block
        when the stderr pipe buffer fills up, causing deadlock.
        """
        if not self._process or not self._process.stderr:
            return

        try:
            async for line in self._process.stderr:
                decoded = line.decode('utf-8', errors='replace').strip()
                if decoded:
                    logger.debug(f"MCP[{self.name}] stderr: {decoded}")
        except asyncio.CancelledError:
            # Normal shutdown
            pass
        except Exception as e:
            logger.debug(f"MCP[{self.name}] stderr monitor stopped: {e}")

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

    async def _send_request(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> dict[str, Any] | None:
        """
        Send a JSON-RPC request and wait for response.

        CRITICAL-1 FIX: Uses Future-based correlation to handle concurrent requests.
        """
        if not self._process or not self._process.stdin:
            return None

        msg_id = self._message_id
        self._message_id += 1

        # Create future for this request
        future: asyncio.Future = asyncio.Future()
        self._pending_requests[msg_id] = future

        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params
        }

        try:
            # Send request
            request_data = json.dumps(request) + "\n"
            self._process.stdin.write(request_data.encode())
            await self._process.stdin.drain()

            # Wait for response (read loop will resolve the future)
            result = await asyncio.wait_for(future, timeout=timeout)
            return result

        except asyncio.TimeoutError:
            self._pending_requests.pop(msg_id, None)
            logger.error(f"MCP server '{self.name}' request timeout: {method}")
            return None
        except Exception as e:
            self._pending_requests.pop(msg_id, None)
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
