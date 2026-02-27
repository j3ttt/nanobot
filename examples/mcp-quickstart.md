# MCP Quick Start Guide

Get started with Model Context Protocol in nanobot in 5 minutes.

## Step 1: Install Node.js

MCP servers are typically Node.js packages. Install Node.js 18+:

```bash
# macOS
brew install node

# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# Windows
# Download from https://nodejs.org/
```

Verify installation:
```bash
node --version  # Should show v18 or higher
npm --version
```

## Step 2: Enable MCP in Config

Edit `~/.nanobot/config.json` and add:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxxxx"
    }
  },
  "mcp": {
    "enabled": true,
    "servers": {}
  }
}
```

## Step 3: Add Your First MCP Server

Let's add the filesystem server to read/write files:

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
          "/Users/your_username/Documents"
        ],
        "env": {}
      }
    }
  }
}
```

**Important**: Replace `/Users/your_username/Documents` with a path you want the agent to access.

## Step 4: Test the Connection

```bash
nanobot mcp test filesystem
```

Expected output:
```
Testing connection to 'filesystem'...
✓ Connected successfully
  Tools: 8
  Resources: 0
  Prompts: 0

Available tools:
  • read_file: Read the complete contents of a file from the file...
  • write_file: Create a new file or completely overwrite an e...
  • list_directory: List all files and directories in a specif...
  ...
```

## Step 5: Start the Gateway

```bash
nanobot gateway
```

Look for these lines:
```
MCP initialized: 1 servers connected
✓ Channels enabled: ...
```

## Step 6: Chat with Your Agent

```bash
# In another terminal
nanobot agent
```

Try these commands:

**List files**:
```
> List all files in the Documents directory
```

The agent will use `mcp_filesystem_list_directory` to show your files.

**Read a file**:
```
> Read the contents of hello.txt
```

The agent will use `mcp_filesystem_read_file`.

**Create a file**:
```
> Create a new file called test.txt with the content "Hello from nanobot!"
```

The agent will use `mcp_filesystem_write_file`.

## Common MCP Servers

### GitHub

Access your repositories:

```json
{
  "github": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": {
      "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxxxx"
    }
  }
}
```

Get a token at: https://github.com/settings/tokens

**Usage**:
- "Search for Python repositories with over 1000 stars"
- "Show me the README of nanobot/nanobot"
- "Create an issue in my-repo titled 'Bug Report'"

### PostgreSQL

Query your database:

```json
{
  "postgres": {
    "command": "npx",
    "args": [
      "-y",
      "@modelcontextprotocol/server-postgres",
      "postgresql://user:password@localhost:5432/mydb"
    ],
    "env": {}
  }
}
```

**Usage**:
- "List all tables in the database"
- "Show me the schema of the users table"
- "How many users do we have?"

### Brave Search

Web search (requires API key from https://brave.com/search/api/):

```json
{
  "brave": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
    "env": {
      "BRAVE_API_KEY": "BSA_xxxxx"
    }
  }
}
```

**Usage**:
- "Search the web for recent AI news"
- "Find Python tutorials for beginners"

### Google Drive

Access cloud files (requires OAuth setup):

```json
{
  "gdrive": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-gdrive"],
    "env": {
      "GDRIVE_CREDENTIALS": "path/to/credentials.json"
    }
  }
}
```

## Troubleshooting

### "Server not starting"

**Check if npx works**:
```bash
npx -y @modelcontextprotocol/server-filesystem --help
```

If it fails, Node.js might not be in PATH. Try:
```bash
which node
which npx
```

### "Connection timeout"

Increase timeout in the code or check server logs:
```bash
nanobot gateway --verbose
```

### "Tool not found"

List all registered tools:
```bash
nanobot status
```

MCP tools are prefixed with `mcp_<server>_<tool>`.

### "Permission denied"

The filesystem server can only access paths you specify in `args`. Add more paths:

```json
{
  "filesystem": {
    "command": "npx",
    "args": [
      "-y",
      "@modelcontextprotocol/server-filesystem",
      "/Users/you/Documents",
      "/Users/you/Projects",
      "/tmp"
    ],
    "env": {}
  }
}
```

## Next Steps

1. **Multiple Servers**: Add GitHub, PostgreSQL, etc. simultaneously
2. **Custom Servers**: Create your own MCP server for custom tools
3. **Resources**: Use MCP resources to load dynamic context
4. **Prompts**: Use MCP prompts for reusable prompt templates

## Learn More

- 📚 [Full MCP Documentation](../nanobot/mcp/README.md)
- 🔧 [Integration Details](../nanobot/mcp/INTEGRATION.md)
- 🌐 [MCP Specification](https://modelcontextprotocol.io)
- 📦 [Available Servers](https://github.com/modelcontextprotocol/servers)

## Example: Multi-Server Setup

Here's a complete config with multiple servers:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5"
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxxxx"
    }
  },
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
        "args": [
          "-y",
          "@modelcontextprotocol/server-postgres",
          "postgresql://localhost/mydb"
        ],
        "env": {}
      }
    }
  }
}
```

Now your agent can:
- Read/write local files
- Access GitHub repos
- Query PostgreSQL databases

All with natural language! 🎉
