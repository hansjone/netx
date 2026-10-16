"""通过真实 stdio MCP 留存本地验收证据；不安装插件、不导入 handler。

使用已有本机 MCP 配置中的凭据，只在子进程内存中传递，证据自动去敏。
示例：python scripts/mcp_acceptance.py ops runNmsDiagnostics '{}'
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "ops": ("netx", "netx_mcp", "netx-mcp"),
    "topology": ("netx-topology", "netx_topology_mcp", "netx-topology-mcp"),
    "biz": ("netx-bizmonitor", "netx_bizmonitor_mcp", "netx-bizmonitor-mcp"),
}
SENSITIVE = re.compile(r"password|passwd|secret|token|authorization|cookie|private_key", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[已去敏]" if SENSITIVE.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, str):
        # MCP 的 JSON 位于 content.text 内；递归处理，避免只净化外层。
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


class McpSession:
    def __init__(self, group, config, evidence, source_root=ROOT):
        source_root = Path(source_root).resolve()
        server_id, module, package = GROUPS[group]
        cfg = json.loads(Path(config).read_text(encoding="utf-8-sig"))
        settings = cfg["mcpServers"][server_id].get("env", {})
        env = os.environ.copy()
        env.update({k: str(v) for k, v in settings.items()})
        # 只允许本机 netx；不能由配置悄悄指向生产服务。
        actual_url = (env.get("NETX_API_URL") or env.get("OCLAW_NETX_BASE_URL")
                      or "http://127.0.0.1:8890").rstrip("/")
        if actual_url not in (
            "http://127.0.0.1:8890", "http://localhost:8890",
            "http://127.0.0.1:8891", "http://localhost:8891"
        ):
            raise ValueError("验收仅允许本机 8890 API 或隔离样例 8891 API")
        env["NETX_API_URL"] = actual_url
        env.pop("OCLAW_NETX_BASE_URL", None)
        env["PYTHONPATH"] = str(source_root / "packages" / package / "src")
        env["PYTHONUTF8"] = "1"
        self.group, self.evidence, self.seq = group, Path(evidence), 0
        self.stderr = []
        self.proc = subprocess.Popen(
            [sys.executable, "-m", module], cwd=source_root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", bufsize=1,
        )
        self.lines = queue.Queue()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "netx-mcp-acceptance", "version": "1"}})
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()

    def _read_stdout(self):
        for line in self.proc.stdout:
            self.lines.put(line)

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr.append(line)

    def rpc(self, method, params=None, timeout=180):
        self.seq += 1
        request = {"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}}
        start = time.monotonic()
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        deadline = start + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TimeoutError(f"{self.group} {method} 超时；stderr={''.join(self.stderr)[-500:]}") from exc
            response = json.loads(line)
            if response.get("id") == self.seq:
                break
            if "id" in response:
                raise RuntimeError("服务器对通知回包或返回了错误的请求 id")
        record = {"at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
                  "group": self.group, "request": request, "response": response,
                  "elapsed_ms": round((time.monotonic() - start) * 1000)}
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
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("tool", help="工具名，或 tools/list")
    parser.add_argument("arguments", nargs="?", default="{}")
    parser.add_argument("--config", default=str(ROOT / "mcp.all.json"))
    parser.add_argument("--source-root", default=str(ROOT), help="被验收包所在 worktree 根目录")
    parser.add_argument("--evidence", default=str(ROOT / "artifacts/mcp-evidence.jsonl"))
    opts = parser.parse_args()
    session = McpSession(opts.group, opts.config, opts.evidence, opts.source_root)
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
