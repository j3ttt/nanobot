"""MCP Resource Loader for context augmentation."""

from typing import Any

from loguru import logger

from nanobot.mcp.manager import MCPManager


class MCPResourceLoader:
    """
    Loads MCP resources into agent context.

    Resources can be referenced by URI and loaded on-demand
    to enrich the agent's context with external data.
    """

    def __init__(self, mcp_manager: MCPManager):
        """
        Initialize resource loader.

        Args:
            mcp_manager: MCP manager for accessing servers.
        """
        self.mcp_manager = mcp_manager

    async def load_resources_for_context(self, resource_uris: list[str]) -> str:
        """
        Load multiple resources and format them for inclusion in context.

        Args:
            resource_uris: List of resource URIs to load.

        Returns:
            Formatted resources content.
        """
        if not resource_uris:
            return ""

        parts = []

        for uri in resource_uris:
            try:
                content = await self.load_resource(uri)
                parts.append(f"## Resource: {uri}\n\n{content}")
            except Exception as e:
                logger.error(f"Failed to load resource '{uri}': {e}")
                parts.append(f"## Resource: {uri}\n\n[Failed to load: {str(e)}]")

        return "\n\n---\n\n".join(parts) if parts else ""

    async def load_resource(self, uri: str) -> str:
        """
        Load a single resource by URI.

        URI format: mcp://server_name/resource_path

        Args:
            uri: Resource URI.

        Returns:
            Resource content as string.
        """
        # Parse URI: mcp://server_name/resource_path
        if not uri.startswith("mcp://"):
            raise ValueError(f"Invalid MCP resource URI: {uri}")

        uri_parts = uri[6:].split("/", 1)
        if len(uri_parts) < 2:
            raise ValueError(f"Invalid MCP resource URI format: {uri}")

        server_name = uri_parts[0]
        resource_path = uri_parts[1]

        # Reconstruct the actual resource URI (without the mcp:// prefix)
        actual_uri = f"file://{resource_path}"  # Or other scheme depending on server

        return await self.mcp_manager.read_resource(server_name, actual_uri)

    def list_available_resources(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """
        List available resources from MCP servers.

        Args:
            server_name: Optional server name to filter by.

        Returns:
            List of resource info dicts.
        """
        resources = []

        clients = [self.mcp_manager.get_client(server_name)] if server_name else self.mcp_manager._clients.values()

        for client in clients:
            if not client:
                continue

            for resource in client.resources:
                resources.append({
                    "server": client.name,
                    "uri": resource.get("uri", ""),
                    "name": resource.get("name", ""),
                    "description": resource.get("description", ""),
                    "mimeType": resource.get("mimeType", "")
                })

        return resources

    def build_resources_summary(self) -> str:
        """
        Build a summary of all available MCP resources.

        This can be included in the agent's system prompt to inform it
        about available resources.

        Returns:
            XML-formatted resources summary.
        """
        resources = self.list_available_resources()

        if not resources:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<mcp-resources>"]

        for r in resources:
            server = escape_xml(r["server"])
            uri = escape_xml(r["uri"])
            name = escape_xml(r["name"])
            desc = escape_xml(r["description"])
            mime = escape_xml(r["mimeType"])

            lines.append(f"  <resource>")
            lines.append(f"    <server>{server}</server>")
            lines.append(f"    <uri>{uri}</uri>")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <mimeType>{mime}</mimeType>")
            lines.append(f"  </resource>")

        lines.append("</mcp-resources>")

        return "\n".join(lines)
