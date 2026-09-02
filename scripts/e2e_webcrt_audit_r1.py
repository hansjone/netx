#!/usr/bin/env python3
"""E2E WebCRT audit test against local netx + a lab device (default R1).

Credentials and target come from the environment (no secrets in-repo)::

    set NETX_PASSWORD=...
    set NETX_BASE=http://127.0.0.1:8890
    set NETX_USER=admin
    set NETX_NE_IP=192.168.0.127
    set NETX_NE_ID=117df1e739d84b46859d425414b07c09
    python scripts/e2e_webcrt_audit_r1.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import requests

try:
    import websockets
except ImportError:
    print("pip install websockets", file=sys.stderr)
    raise

BASE = os.environ.get("NETX_BASE", "http://127.0.0.1:8890").rstrip("/")
R1_IP = os.environ.get("NETX_NE_IP", "192.168.0.127")
R1_NE_ID = os.environ.get("NETX_NE_ID", "117df1e739d84b46859d425414b07c09")
USER = os.environ.get("NETX_USER", "admin")
PWD = os.environ.get("NETX_PASSWORD") or os.environ.get("NETX_PWD", "")


def login() -> str:
    r = requests.post(
        f"{BASE}/v1/auth/login",
        json={"username": USER, "password": PWD},
        timeout=15,
    )
    r.raise_for_status()
    return str(r.json()["access_token"])


def headers(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def create_session(tok: str, ne_id: str) -> dict[str, Any]:
    r = requests.post(
        f"{BASE}/v1/webcrt/sessions",
        headers=headers(tok),
        json={"ne_id": ne_id, "cols": 120, "rows": 40, "async_connect": True},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def ws_ticket(tok: str) -> str:
    r = requests.post(f"{BASE}/v1/webcrt/ws-ticket", headers=headers(tok), timeout=10)
    r.raise_for_status()
    return str(r.json()["ticket"])


def recent_commands(tok: str, session_id: str) -> list[str]:
    r = requests.get(
        f"{BASE}/v1/audit-logs",
        headers=headers(tok),
        params={"action": "webcrt.command", "limit": 40},
        timeout=15,
    )
    r.raise_for_status()
    out: list[str] = []
    for row in r.json().get("items", []):
        detail = row.get("detail") or {}
        if str(detail.get("session_id") or "") != session_id and str(row.get("path") or ""):
            # Prefer session filter via detail when present
            pass
        if str(detail.get("session_id") or "") and str(detail.get("session_id")) != session_id:
            continue
        # Also accept jsonl-sourced detail without session when command looks recent
        cmd = str(detail.get("command") or "").strip()
        if cmd:
            out.append(cmd)
    # Fallback: read audit.jsonl for this session
    if not out:
        try:
            with open("data/webcrt/audit.jsonl", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("event") != "command":
                        continue
                    if obj.get("session_id") != session_id:
                        continue
                    cmd = str(obj.get("command") or "").strip()
                    if cmd:
                        out.append(cmd)
        except OSError:
            pass
    return out


async def ws_run(session_id: str, ticket: str) -> str:
    url = f"{BASE.replace('http://', 'ws://').replace('https://', 'wss://')}/v1/webcrt/sessions/{session_id}/ws?ws_ticket={ticket}"
    transcript = ""
    last_prompt = ""

    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:

        async def recv_until_prompt(timeout: float = 20.0) -> None:
            nonlocal transcript, last_prompt
            deadline = time.time() + timeout
            saw_ready = False
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    if "<r1>" in transcript or "[~r1]" in transcript or "[r1]" in transcript:
                        return
                    if saw_ready and time.time() + 3 > deadline:
                        return
                    continue
                if isinstance(raw, bytes):
                    chunk = raw.decode("utf-8", errors="replace")
                else:
                    msg = json.loads(raw)
                    if msg.get("type") == "status" and msg.get("state") == "error":
                        raise RuntimeError(f"session error: {msg.get('message')}")
                    if msg.get("type") == "status" and msg.get("state") == "ready":
                        saw_ready = True
                        continue
                    if msg.get("type") != "stdout":
                        continue
                    chunk = str(msg.get("data") or "")
                transcript += chunk
                plain = chunk.replace("\r", "\n")
                for ln in plain.split("\n"):
                    s = ln.strip()
                    if s.startswith("<r1>") or s.startswith("[~r1]") or s.startswith("[r1]"):
                        last_prompt = s
                if "<r1>" in transcript or "[~r1]" in transcript or "[r1]" in transcript:
                    return

        async def drain(ms: float = 0.6) -> None:
            nonlocal transcript, last_prompt
            end = time.time() + ms
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.15)
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    chunk = raw.decode("utf-8", errors="replace")
                else:
                    msg = json.loads(raw)
                    if msg.get("type") != "stdout":
                        continue
                    chunk = str(msg.get("data") or "")
                transcript += chunk
                for ln in chunk.replace("\r", "\n").split("\n"):
                    s = ln.strip()
                    if s.startswith("<r1>") or s.startswith("[~r1]") or s.startswith("[r1]"):
                        last_prompt = s

        async def send_stdin(data: str, audit_line: str | None = None) -> None:
            payload: dict[str, Any] = {"type": "stdin", "data": data}
            if audit_line:
                payload["audit_line"] = audit_line[:512]
            await ws.send(json.dumps(payload))

        await recv_until_prompt(45.0)
        await drain(1.5)
        if "<r1>" not in transcript and "[~r1]" not in transcript and "[r1]" not in transcript:
            # Nudge device for prompt (same as UI prompt_sync Enter).
            await send_stdin("\r")
            await drain(2.0)
        if "<r1>" not in transcript and "[~r1]" not in transcript and "[r1]" not in transcript:
            raise RuntimeError(f"no R1 prompt in transcript tail: {transcript[-500:]!r}")

        # --- Case 1: Tab completion with frontend audit_line (xterm ground truth) ---
        await send_stdin("dis ip int\t")
        await drain(1.2)
        visible = last_prompt if "display" in last_prompt.lower() or "interface" in last_prompt.lower() else "<r1>display ip interface brief"
        # Huawei often expands to "display ip interface brief"
        if "display" not in visible.lower():
            # keep whatever device showed on the prompt line
            visible = last_prompt or visible
        await send_stdin("\r", visible)
        await drain(2.0)

        # --- Case 2: Backspace/insert-style edit — wrong stdin, correct audit_line ---
        # Simulate operator seeing "<r1>display version" after fixing typos.
        await send_stdin("disX\x08play version\r", "<r1>display version")
        await drain(2.0)

        # --- Case 3: Mid-line insert disagreement — audit_line must win ---
        await send_stdin("dis version\r", "<r1>display version")
        await drain(2.0)

        # --- Case 4: Tab without audit_line — rely on device echo / prompt_hint ---
        await send_stdin("dis ll n b\t")
        await drain(1.5)
        await send_stdin("\r")  # no audit_line
        await drain(2.5)

        await ws.send(json.dumps({"type": "close"}))
    return transcript


def main() -> int:
    if not PWD:
        print(
            "Set NETX_PASSWORD (or NETX_PWD) before running this script.",
            file=sys.stderr,
        )
        return 2
    print("login...")
    tok = login()
    ne_id = R1_NE_ID
    print(f"ne_id={ne_id} ip={R1_IP}")

    sess = create_session(tok, ne_id)
    sid = str(sess["session_id"])
    print(f"session={sid} ne_ip={sess.get('ne_ip') or sess.get('ip')}")

    ticket = ws_ticket(tok)
    print("websocket test...")
    transcript = asyncio.run(ws_run(sid, ticket))
    print("transcript_tail:", repr(transcript[-300:]))

    time.sleep(1.0)
    cmds = recent_commands(tok, sid)
    # Also always print jsonl session commands
    jsonl_cmds: list[str] = []
    try:
        with open("data/webcrt/audit.jsonl", encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("event") == "command" and obj.get("session_id") == sid:
                    jsonl_cmds.append(str(obj.get("command") or ""))
    except OSError:
        pass
    cmds = jsonl_cmds or cmds

    print("\n=== session webcrt.command audits ===")
    for c in cmds:
        print(repr(c))

    failures: list[str] = []
    joined = "\n".join(cmds)
    if "\t" in joined:
        failures.append("literal Tab leaked into audit")
    if "dislayplay" in joined or "disX" in joined:
        failures.append("backspace/edit corruption recorded")
    if not any("display ip interface" in c.lower() or "ip interface brief" in c.lower() for c in cmds):
        failures.append("tab+audit_line did not record display ip interface brief")
    if not any("display version" in c for c in cmds):
        failures.append("display version not audited correctly")
    if any(c.strip() in ("dis version", "<r1>dis version") for c in cmds):
        failures.append("audit_line ignored for insert/disagreement case")
    if not any("dis ll n" in c for c in cmds):
        failures.append("missing dis ll n b audit")

    if failures:
        print("\nFAIL:", "; ".join(failures), file=sys.stderr)
        return 2
    print("\nOK: audit scenarios look correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
