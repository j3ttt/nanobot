# MCP Integration for nanobot

This module integrates [Model Context Protocol (MCP)](https://modelcontextprotocol.io) support into nanobot, allowing the agent to connect to external MCP servers and use their tools and resources.

## 🎯 What is MCP?

**Model Context Protocol (MCP)** is an open standard for connecting AI assistants to external data sources and tools. It enables:

- **Tools**: External functions the agent can call (e.g., file operations, database queries)
- **Resources**: Data the agent can read (e.g., files, API responses)
- **Prompts**: Reusable prompt templates with parameters

## 🏗️ Architecture

```
┌─────────────────────────────────────────┐
│         nanobot AgentLoop               │
│  ┌────────────┐      ┌──────────────┐  │
│  │ToolRegistry│◄─────┤ MCP Manager  │  │
│  └────────────┘      └──────┬───────┘  │
│         ▲                    │          │
│         │                    ▼          │
│         │            ┌──────────────┐  │
│         └────────────┤ MCP Client   │  │
│                      └──────┬───────┘  │
└─────────────────────────────┼──────────┘
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
         ┌───────────────┐        ┌───────────────┐
         │  MCP Server 1 │        │  MCP Server 2 │
         │  (filesystem) │        │  (database)   │
         └───────────────┘        └───────────────┘
```

### Components

1. **MCPClient** (`client.py`): Connects to individual MCP servers via stdio transport
2. **MCPManager** (`manager.py`): Manages multiple MCP servers and registers their tools
3. **MCPToolWrapper** (`tool_wrapper.py`): Wraps MCP tools as nanobot Tools
4. **MCPResourceLoader** (`resource_loader.py`): Loads MCP resources into agent context

## 📦 Installation

MCP servers are typically Node.js packages. Install Node.js 18+ first:

```bash
# macOS
brew install node

# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

## ⚙️ Configuration

Add MCP servers to your `~/.nanobot/config.json`:

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/Documents"],
        "env": {}
      },
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxxxx"
        }
      }
    }
  }
}
```

### Configuration Options

- `enabled`: Enable/disable MCP support
- `servers`: Dictionary of MCP servers to connect to
  - **name**: Server identifier (used in tool names)
  - `command`: Command to start the server (e.g., `npx`, `node`, `python`)
  - `args`: Command-line arguments
  - `env`: Environment variables for the server process

## 🚀 Usage

### Starting the Gateway

```bash
nanobot gateway
```

The gateway will automatically:
1. Connect to all configured MCP servers
2. Discover available tools from each server
3. Register them in the ToolRegistry
4. Make them available to the agent

### Tool Naming

MCP tools are automatically prefixed with `mcp_{server_name}_`:

- Server: `filesystem`
- Original tool: `read_file`
- nanobot tool name: `mcp_filesystem_read_file`

### Using MCP Tools

The agent can use MCP tools just like built-in tools:

```
User: Read the file /path/to/file.txt using the MCP filesystem server

Agent: I'll use the mcp_filesystem_read_file tool...
```

### Listing Available Tools

```bash
nanobot status
```

This will show all registered tools, including MCP tools.

## 🔌 Available MCP Servers

### Official Servers

| Server | Package | Description |
|--------|---------|-------------|
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | Read/write files in allowed directories |
| **GitHub** | `@modelcontextprotocol/server-github` | Search repos, read files, create issues |
| **GitLab** | `@modelcontextprotocol/server-gitlab` | GitLab API integration |
| **Google Drive** | `@modelcontextprotocol/server-gdrive` | Access Google Drive files |
| **Postgres** | `@modelcontextprotocol/server-postgres` | Query PostgreSQL databases |
| **SQLite** | `@modelcontextprotocol/server-sqlite` | Query SQLite databases |
| **Brave Search** | `@modelcontextprotocol/server-brave-search` | Web search via Brave API |
| **Fetch** | `@modelcontextprotocol/server-fetch` | HTTP requests |

### Community Servers

Find more servers at: https://github.com/modelcontextprotocol/servers

## 📝 Example Configurations

### Filesystem Server

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "filesystem": {
        "command": "npx",
        "args": [
          "-y",
          "@modelcontextprotocol/server-filesystem",
          "/Users/you/Documents",
          "/Users/you/Projects"
        ],
        "env": {}
      }
    }
  }
}
```

**Tools provided**: `read_file`, `write_file`, `list_directory`, `create_directory`, etc.

### GitHub Server

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxxxx"
        }
      }
    }
  }
}
```

**Tools provided**: `search_repositories`, `create_or_update_file`, `create_issue`, etc.

### PostgreSQL Server

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "postgres": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/db"],
        "env": {}
      }
    }
  }
}
```

**Tools provided**: `query`, `list_tables`, `describe_table`, etc.

### Multiple Servers

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/docs"],
        "env": {}
      },
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxxxx"
        }
      },
      "postgres": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"],
        "env": {}
      }
    }
  }
}
```

## 🔒 Security

### Workspace Restriction

When `tools.restrictToWorkspace` is enabled, MCP tools are **not** restricted to the workspace directory. Be careful when:

- Granting filesystem access outside the workspace
- Connecting to production databases
- Using servers with destructive operations

### API Keys and Credentials

- Store sensitive credentials in `env` section
- Never commit credentials to version control
- Use environment variables for secrets:

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
          "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
        }
      }
    }
  }
}
```

Then set: `export GITHUB_TOKEN=ghp_xxxxx`

## 🐛 Troubleshooting

### Server Not Starting

**Problem**: MCP server fails to start

**Solution**:
1. Check if the command exists: `which npx`
2. Try running the command manually:
   ```bash
   npx -y @modelcontextprotocol/server-filesystem /path
   ```
3. Check nanobot logs: `nanobot gateway --verbose`

### Tool Not Available

**Problem**: Agent says "tool not found"

**Solution**:
1. Check if server is connected:
   ```bash
   nanobot status
   ```
2. Look for errors in gateway logs
3. Verify tool name format: `mcp_{server}_{tool}`

### Permission Denied

**Problem**: MCP server can't access files

**Solution**:
1. Check file permissions
2. Verify paths in server configuration
3. For filesystem server, ensure paths are absolute

## 🔄 Protocol Details

### Communication Flow

1. **Initialize**: nanobot starts MCP server subprocess
2. **Handshake**: Exchange capabilities (tools, resources, prompts)
3. **Discovery**: nanobot registers available tools
4. **Execution**: Agent calls tools via JSON-RPC

### Transport

Currently supported: **stdio** (stdin/stdout)

Future support: **SSE** (HTTP Server-Sent Events)

### Message Format

JSON-RPC 2.0:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "read_file",
    "arguments": {
      "path": "/path/to/file.txt"
    }
  }
}
```

## 📚 Resources

- [MCP Specification](https://spec.modelcontextprotocol.io)
- [MCP SDKs](https://github.com/modelcontextprotocol/sdk)
- [Official Servers](https://github.com/modelcontextprotocol/servers)
- [Create Your Own Server](https://modelcontextprotocol.io/docs/creating-servers)

## 🤝 Contributing

Want to add more features?

- Support for SSE transport
- Resource templates in prompts
- Prompt management UI
- Server health monitoring

PRs welcome! 🎉
