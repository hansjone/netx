"""Backward-compatible entry for ``python netx_api/mcp_server.py`` / ``netx-mcp`` script on netx-ops install.

HTTP MCP: delegates to ``netx_mcp`` (``pip install -e packages/netx-mcp``).
Legacy DB mode (``NETX_MCP_MODE=db``): requires full ``netx-ops`` install only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Script-path launch without pip install netx-mcp: add packages/netx-mcp/src
_repo_root = Path(__file__).resolve().parent.parent
_mcp_src = _repo_root / "packages" / "netx-mcp" / "src"
if _mcp_src.is_dir() and str(_mcp_src) not in sys.path:
    sys.path.insert(0, str(_mcp_src))


def main() -> None:
    if str(os.getenv("NETX_MCP_MODE") or "http").strip().lower() == "db":
        from netx_api.mcp.db_server import run_stdio_loop

        run_stdio_loop()
        return
    from netx_mcp.server import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    main()
