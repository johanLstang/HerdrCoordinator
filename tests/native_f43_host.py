"""Empty, isolated MCP catalog for the F43 fixed JSON native probes. No delegation/tools."""

from mcp.server.fastmcp import FastMCP

if __name__ == "__main__":
    FastMCP("f43-fixed-json-probe").run(transport="stdio")
