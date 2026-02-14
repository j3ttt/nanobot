# MCP Integration Implementation Details

## Design Philosophy

The MCP integration for nanobot follows these principles:

1. **Lightweight**: Minimal code, no heavy dependencies
2. **Plugin-like**: MCP servers are optional and don't affect core functionality
3. **Transparent**: MCP tools appear as regular tools to the agent
4. **Flexible**: Easy to add/remove servers at runtime

## Code Structure

### Module Layout

```
nanobot/mcp/
├── __init__.py           # Module exports
├── client.py             # MCPClient - connects to one server
├── manager.py            # MCPManager - manages multiple servers
├── tool_wrapper.py       # MCPToolWrapper - adapts MCP tools
├── resource_loader.py    # MCPResourceLoader - loads resources
└── README.md             # User documentation
```

### Key Classes

#### 1. MCPClient (`client.py`)

**Responsibility**: Low-level communication with a single MCP server

**Key Methods**:
- `connect()`: Start server subprocess and initialize
- `call_tool()`: Execute a tool via JSON-RPC
- `read_resource()`: Fetch a resource by URI
- `get_prompt()`: Retrieve a prompt template

**Transport**: stdio (stdin/stdout via subprocess)

**Protocol**: JSON-RPC 2.0

#### 2. MCPManager (`manager.py`)

**Responsibility**: High-level management of multiple MCP servers

**Key Methods**:
- `add_server()`: Connect to a new server
- `remove_server()`: Disconnect and cleanup
- `initialize_from_config()`: Bulk setup from config
- `_register_server_tools()`: Wrap and register tools

**Integration Point**: Receives `ToolRegistry` and registers wrapped tools

#### 3. MCPToolWrapper (`tool_wrapper.py`)

**Responsibility**: Adapter between MCP protocol and nanobot Tool interface

**Key Methods**:
- `name`: Returns `mcp_{server}_{tool}` format
- `description`: Prefixed with `[MCP:server]`
- `parameters`: JSON Schema from MCP (pass-through)
- `execute()`: Calls `client.call_tool()` and formats result

**Result Formatting**: Handles text, images, resources from MCP

#### 4. MCPResourceLoader (`resource_loader.py`)

**Responsibility**: Load MCP resources into agent context

**Key Methods**:
- `load_resource()`: Fetch single resource
- `load_resources_for_context()`: Batch load with formatting
- `build_resources_summary()`: XML summary for system prompt

**URI Format**: `mcp://server_name/resource_path`

## Integration with AgentLoop

### Initialization Flow

```python
# 1. AgentLoop.__init__
self.mcp_config = mcp_config
self.mcp_manager = None

# 2. AgentLoop.run()
if self.mcp_config and self.mcp_config.get("enabled"):
    await self._initialize_mcp()

# 3. AgentLoop._initialize_mcp()
self.mcp_manager = MCPManager(self.tools)
await self.mcp_manager.initialize_from_config(self.mcp_config)

# Result: All MCP tools are now in self.tools
```

### Tool Registration Flow

```python
# MCPManager.add_server()
client = MCPClient(name, command, args, env)
await client.connect()  # Discovers tools

# For each tool:
for tool_def in client.tools:
    wrapper = MCPToolWrapper(client, tool_def["name"], ...)
    self.tool_registry.register(wrapper)
```

### Tool Execution Flow

```python
# 1. LLM calls tool: "mcp_filesystem_read_file"
# 2. ToolRegistry.execute()
# 3. MCPToolWrapper.execute()
# 4. MCPClient.call_tool()
# 5. JSON-RPC request → MCP server
# 6. JSON-RPC response ← MCP server
# 7. Format result → return to agent
```

## Configuration Schema

### Top-Level MCP Config

```python
class MCPServerConfig(BaseModel):
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

class MCPConfig(BaseModel):
    enabled: bool = False
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
```

### Config Loading

```python
# Load from ~/.nanobot/config.json
config = load_config()

# Extract MCP config
mcp_config = {
    "enabled": config.mcp.enabled,
    "servers": {
        name: {
            "command": srv.command,
            "args": srv.args,
            "env": srv.env
        }
        for name, srv in config.mcp.servers.items()
    }
}

# Pass to AgentLoop
agent = AgentLoop(..., mcp_config=mcp_config)
```

## Error Handling

### Connection Failures

```python
# MCPClient.connect()
try:
    self._process = await asyncio.create_subprocess_exec(...)
    await self._send_request("initialize", {...})
except Exception as e:
    logger.error(f"Failed to connect to MCP server '{self.name}': {e}")
    return False
```

**Behavior**: Non-fatal. Gateway continues without that server.

### Tool Execution Errors

```python
# MCPToolWrapper.execute()
try:
    result = await self.client.call_tool(self.tool_name, kwargs)
    return self._format_result(result)
except Exception as e:
    logger.error(f"MCP tool '{self.name}' execution failed: {e}")
    return f"Error executing MCP tool '{self.tool_name}': {str(e)}"
```

**Behavior**: Returns error message to agent (like built-in tools)

### Timeout Handling

```python
# MCPClient._send_request()
response_line = await asyncio.wait_for(
    self._process.stdout.readline(),
    timeout=30.0
)
```

**Timeout**: 30 seconds per request

## Performance Considerations

### Subprocess Overhead

- Each MCP server runs as a subprocess
- stdio transport has minimal overhead
- No network latency (local IPC)

### Caching

- Tools/resources are discovered once at startup
- No re-discovery on each use
- Client maintains connection throughout session

### Concurrency

- Multiple MCP servers run concurrently
- Each has independent subprocess
- Tool calls are async and don't block

## Security Model

### Process Isolation

- MCP servers run as child processes
- Inherit parent's user permissions
- Cannot access more than parent process

### Path Restrictions

- `restrictToWorkspace` **does not** apply to MCP tools
- MCP filesystem server controls its own path restrictions
- Configure allowed paths in MCP server args

### Credential Management

- Environment variables passed per-server
- No global credential leakage
- Secrets in config file (use environment variables)

## Testing Strategy

### Unit Tests

```python
# Test MCPClient connection
async def test_mcp_client_connect():
    client = MCPClient("test", "echo", [])
    assert await client.connect()
    assert client.is_connected

# Test tool wrapping
def test_mcp_tool_wrapper():
    wrapper = MCPToolWrapper(client, "test_tool", ...)
    assert wrapper.name == "mcp_test_test_tool"
    assert "[MCP:test]" in wrapper.description
```

### Integration Tests

```python
# Test end-to-end flow
async def test_mcp_integration():
    config = {"enabled": True, "servers": {...}}
    manager = MCPManager(tool_registry)
    await manager.initialize_from_config(config)

    # Verify tools registered
    assert "mcp_filesystem_read_file" in tool_registry

    # Execute tool
    result = await tool_registry.execute(
        "mcp_filesystem_read_file",
        {"path": "/tmp/test.txt"}
    )
    assert "content" in result
```

## Future Enhancements

### SSE Transport

```python
class MCPSSEClient(MCPClient):
    """SSE-based transport for HTTP MCP servers."""

    async def connect(self) -> bool:
        self._session = aiohttp.ClientSession()
        self._stream = await self._session.get(self.url)
        # Listen for SSE events...
```

### Hot Reload

```python
# Watch config file for changes
async def watch_config():
    while True:
        if config_changed():
            await manager.reload_servers()
        await asyncio.sleep(5)
```

### Server Health Monitoring

```python
# Periodic health checks
async def monitor_health():
    for client in manager._clients.values():
        if not await client.ping():
            logger.warning(f"Server {client.name} unhealthy")
            await manager.restart_server(client.name)
```

### Resource Caching

```python
# Cache frequently accessed resources
class CachedResourceLoader(MCPResourceLoader):
    def __init__(self):
        self._cache = {}

    async def load_resource(self, uri: str) -> str:
        if uri in self._cache:
            return self._cache[uri]
        result = await super().load_resource(uri)
        self._cache[uri] = result
        return result
```

## References

- [MCP Specification](https://spec.modelcontextprotocol.io)
- [JSON-RPC 2.0](https://www.jsonrpc.org/specification)
- [nanobot Tool System](../agent/tools/README.md)
