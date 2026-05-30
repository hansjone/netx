"""Backward-compatible re-exports; prefer ``pip install netx-mcp`` and ``import netx_mcp``."""

from netx_mcp.http_tools import HTTP_MCP_TOOLS, call_http_tool

__all__ = ["HTTP_MCP_TOOLS", "call_http_tool"]
