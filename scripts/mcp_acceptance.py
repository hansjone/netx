"""通过真实远程 HTTP MCP（/mcp Streamable HTTP）留存本地验收证据。

使用已有本机 MCP 配置中的凭据，只在进程内存中传递，证据自动去敏。
示例：python scripts/mcp_acceptance.py ops runNmsDiagnostics '{}'
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "ops": "ops",
    "topology": "topology",
    "biz": "bizMonitor",
}
SENSITIVE = re.compile(r"password|passwd|secret|token|authorization|cookie|private_key", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[已去敏]" if SENSITIVE.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return re.sub(r"\bnxt_[\w-]+", "[已去敏]", value)
        if isinstance(decoded, (dict, list)):
            return json.dumps(redact(decoded), ensure_ascii=False)
    return value


def payload(response):
    result = response.get("result", {})
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except ValueError:
            pass
    return result or response.get("error", {})


def _resolve_base(config: Path) -> tuple[str, str]:
    cfg = json.loads(Path(config).read_text(encoding="utf-8-sig"))
    servers = cfg.get("mcpServers") or {}
    # Prefer first remote URL entry; fall back to env.
    url = ""
    token = os.environ.get("NETX_API_TOKEN") or os.environ.get("NETX_MCP_TOKEN") or ""
    for _name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        candidate = str(entry.get("url") or "").strip()
        if candidate.startswith("http"):
            url = candidate
            headers = entry.get("headers") or {}
            auth = str(headers.get("Authorization") or headers.get("authorization") or "")
            if auth.lower().startswith("bearer "):
                raw = auth[7:].strip()
                if raw and not raw.startswith("${"):
                    token = raw
            break
    if not url:
        url = (os.environ.get("NETX_API_URL") or "http://127.0.0.1:8890").rstrip("/") + "/mcp"
    # Strip query for base; group applied separately.
    base = url.split("?", 1)[0].rstrip("/")
    allowed = {
        "http://127.0.0.1:8890/mcp",
        "http://localhost:8890/mcp",
        "http://127.0.0.1:8891/mcp",
        "http://localhost:8891/mcp",
    }
    if base not in allowed:
        raise ValueError("验收仅允许本机 8890/8891 的 /mcp")
    if not token:
        for p in (
            Path(os.environ.get("NETX_AUTH_MCP_TOKEN_FILE") or ""),
            ROOT / "data" / "auth" / "mcp_token",
        ):
            if p.is_file():
                token = p.read_text(encoding="utf-8").strip()
                if token:
                    break
    if not token:
        raise ValueError("缺少 Bearer token（NETX_API_TOKEN 或 data/auth/mcp_token）")
    return base, token


class McpSession:
    def __init__(self, group: str, config: str, evidence: str):
        import httpx

        self.group = GROUPS[group]
        self.evidence = Path(evidence)
        self.seq = 0
        base, token = _resolve_base(Path(config))
        self.url = f"{base}?groups={self.group}"
        self._client = httpx.Client(
            timeout=180.0,
            trust_env=False,  # Windows system proxy breaks loopback /mcp framing
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2024-11-05",
            },
        )
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "netx-mcp-acceptance", "version": "1"},
            },
        )
        # notifications/initialized — no response expected
        self._client.post(
            self.url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    def rpc(self, method, params=None):
        import time

        self.seq += 1
        request = {"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}}
        start = time.monotonic()
        r = self._client.post(self.url, json=request)
        r.raise_for_status()
        response = r.json()
        record = {
            "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
            "group": self.group,
            "request": request,
            "response": response,
            "elapsed_ms": round((time.monotonic() - start) * 1000),
        }
        self.evidence.parent.mkdir(parents=True, exist_ok=True)
        with self.evidence.open("a", encoding="utf-8") as f:
            f.write(json.dumps(redact(record), ensure_ascii=False) + "\n")
        return response

    def call(self, name, args):
        if name == "execManagedNe":
            commands = list(args.get("commands") or [])
            for target in args.get("targets") or []:
                commands.extend(target.get("commands") or [])
            for cmd in commands:
                if not re.match(r"(?i)^(show|display)\s+", cmd) or re.search(r"[;\r\n`$&<>|]", cmd):
                    raise ValueError("验收 CLI 只允许单行 show/display")
        return self.rpc("tools/call", {"name": name, "arguments": args})

    def close(self):
        self._client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("tool", help="工具名，或 tools/list")
    parser.add_argument("arguments", nargs="?", default="{}")
    parser.add_argument("--config", default=str(ROOT / "mcp.all.json"))
    parser.add_argument("--evidence", default=str(ROOT / "artifacts/mcp-evidence.jsonl"))
    opts = parser.parse_args()
    session = McpSession(opts.group, opts.config, opts.evidence)
    try:
        if opts.tool == "tools/list":
            response = session.rpc("tools/list")
            names = [t["name"] for t in response.get("result", {}).get("tools", [])]
            print(json.dumps({"count": len(names), "tools": names}, ensure_ascii=False))
        else:
            response = session.call(opts.tool, json.loads(opts.arguments))
            print(json.dumps(redact(payload(response)), ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
