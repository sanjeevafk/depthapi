"""Agent Model Context Protocol (MCP) in-tree server for DepthAPI."""
from typing import Any

__all__ = ["DepthApiMcpServer"]


def __getattr__(name: str) -> Any:
    if name == "DepthApiMcpServer":
        from api.mcp.server import DepthApiMcpServer
        return DepthApiMcpServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
