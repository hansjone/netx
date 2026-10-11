#!/usr/bin/env python3
"""WebCRT latency / throughput / audit benchmark against a live netx + lab device.

    python scripts/bench_webcrt.py --ne-id <id> [--prompt-re "<r1>|\\[~?r1\\]"] [--audit]

Measures (client-side, real WebSocket):
  * connect_ms           create session -> first prompt visible
  * echo latency         single keystroke -> echoed byte (p50/p95/max)
  * burst echo latency   20 keys sent 3ms apart -> last echo byte
  * command RTT          Enter -> next prompt for a short command
  * bulk throughput      long output command, bytes / seconds
With --audit it also types a set of tricky command lines (backspace, tab, history
recall, bare Enter) and prints what landed in audit.jsonl for the session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time

import httpx
import websockets

BASE = os.environ.get("NETX_BASE", "http://127.0.0.1:8890").rstrip("/")
USER = os.environ.get("NETX_USER", "admin")
PWD = os.environ.get("NETX_PASSWORD", "Admin@1234")


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ne-id", default="")
    ap.add_argument("--quick", default="", help="ip:port:user:pass -> quick-connect instead of --ne-id")
    ap.add_argument("--prompt-re", default=r"(?:<[^>\r\n]+>|\[[^\]\r\n]+\]|[\w.\-]+(?:\([^)]+\))*[#>])\s*$")
    ap.add_argument("--bulk-cmd", default="display current-configuration")
    ap.add_argument("--probe-cmd", default="display clock")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--echo-only", action="store_true", help="only connect + echo latency")
    ap.add_argument("--audit-cmds", default="")
    ap.add_argument("--audit-file", default=os.path.join("data", "webcrt", "audit.jsonl"))
    args = ap.parse_args()
    prompt_re = re.compile(args.prompt_re)

    c = httpx.Client(base_url=BASE, timeout=60)
    tok = c.post("/v1/auth/login", json={"username": USER, "password": PWD}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    t_create = time.perf_counter()
    if args.quick:
        ip, port, user, pw = args.quick.split(":", 3)
        r = c.post("/v1/webcrt/sessions/quick-connect", headers=h, json={
            "name": "bench", "ip_address": ip, "port": int(port), "protocol": "ssh", "username": user,
            "password": pw, "save_password": False, "cols": 160, "rows": 50})
    else:
        r = c.post("/v1/webcrt/sessions", headers=h, json={"ne_id": args.ne_id, "cols": 160, "rows": 50})
    r.raise_for_status()
    sid = r.json()["session_id"]
    ticket = c.post("/v1/webcrt/ws-ticket", headers=h).json()["ticket"]
    url = f"{BASE.replace('http', 'ws', 1)}/v1/webcrt/sessions/{sid}/ws?ws_ticket={ticket}"
    print(f"session={sid}")

    async with websockets.connect(url, max_size=64 * 1024 * 1024) as ws:
        buf = ""
        ev = asyncio.Event()
        last_rx = 0.0
        rx_bytes = 0

        async def reader() -> None:
            nonlocal buf, last_rx, rx_bytes
            dec = __import__("codecs").getincrementaldecoder("utf-8")(errors="replace")
            async for raw in ws:
                if isinstance(raw, bytes):
                    buf += dec.decode(raw)
                    rx_bytes += len(raw)
                    last_rx = time.perf_counter()
                    ev.set()
                else:
                    m = json.loads(raw)
                    if m.get("type") == "status" and m.get("state") in ("error", "closed", "warning"):
                        print("STATUS", m)
                    if m.get("type") == "stdout":
                        buf += m.get("data", "")
                        last_rx = time.perf_counter()
                        ev.set()

        rt = asyncio.create_task(reader())

        def at_prompt() -> bool:
            tail = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf).replace("\r", "\n").rstrip(" ").split("\n")[-1]
            return bool(prompt_re.search(tail)) and len(tail) < 80

        async def wait_prompt(timeout: float = 60.0, quiet: float = 0.05) -> bool:
            end = time.perf_counter() + timeout
            while time.perf_counter() < end:
                if at_prompt() and time.perf_counter() - last_rx >= quiet:
                    return True
                await asyncio.sleep(0.005)
            return False

        async def send(data: str, audit_line: str | None = None) -> None:
            p = {"type": "stdin", "data": data}
            if audit_line:
                p["audit_line"] = audit_line
            await ws.send(json.dumps(p))

        ok = await wait_prompt(90, 0.3)
        connect_ms = (time.perf_counter() - t_create) * 1000
        if not ok:
            await send("\r")
            ok = await wait_prompt(15, 0.3)
        print(f"connect_ms={connect_ms:.0f} prompt={'yes' if ok else 'NO'} tail={buf[-80:]!r}")
        if not ok:
            return 2

        # --- single-key echo latency ---
        lat: list[float] = []
        for ch in "abcdefghijklmnopqrst":
            n0 = rx_bytes
            t0 = time.perf_counter()
            await send(ch)
            while rx_bytes == n0 and time.perf_counter() - t0 < 2:
                await asyncio.sleep(0.0005)
            lat.append((time.perf_counter() - t0) * 1000)
            await asyncio.sleep(0.05)
        await send("\x08" * 20)
        await asyncio.sleep(0.4)
        print(f"echo_ms p50={statistics.median(lat):.1f} p95={pct(lat, .95):.1f} max={max(lat):.1f}")

        if args.echo_only:
            await ws.send(json.dumps({"type": "close"}))
            await asyncio.sleep(0.3)
            rt.cancel()
            return 0

        # --- burst typing: last echo arrival ---
        await wait_prompt(5, 0.1)
        n0 = len(buf)
        t0 = time.perf_counter()
        for ch in "abcdefghijklmnopqrst":
            await send(ch)
            await asyncio.sleep(0.003)
        t_sent = time.perf_counter()
        while time.perf_counter() - t_sent < 3 and not buf.endswith("abcdefghijklmnopqrst"):
            await asyncio.sleep(0.002)
        print(f"burst_echo_ms={(time.perf_counter() - t_sent) * 1000:.1f} (after last key sent)")
        await send("\x08" * 20)
        await asyncio.sleep(0.4)

        # --- command RTT ---
        rtts: list[float] = []
        for _ in range(5):
            await wait_prompt(10, 0.05)
            n0 = len(buf)
            t0 = time.perf_counter()
            await send(args.probe_cmd + "\r")
            await asyncio.sleep(0.02)
            await wait_prompt(15, 0.0)
            # prompt re-appeared after output
            while len(buf) == n0 and time.perf_counter() - t0 < 10:
                await asyncio.sleep(0.005)
            rtts.append((time.perf_counter() - t0) * 1000)
        print(f"cmd_rtt_ms p50={statistics.median(rtts):.0f} max={max(rtts):.0f} ({args.probe_cmd})")

        # --- bulk ---
        await wait_prompt(10, 0.2)
        rx0 = rx_bytes
        t0 = time.perf_counter()
        await send(args.bulk_cmd + "\r")
        await asyncio.sleep(0.2)
        # page through "More" prompts if any
        deadline = time.perf_counter() + 90
        while time.perf_counter() < deadline:
            tail = buf[-200:]
            if "More" in tail[-40:]:
                await send(" ")
                await asyncio.sleep(0.01)
                continue
            if at_prompt() and time.perf_counter() - last_rx > 0.4 and rx_bytes - rx0 > 100:
                break
            await asyncio.sleep(0.01)
        dt = time.perf_counter() - t0 - 0.4
        print(f"bulk bytes={rx_bytes - rx0} secs={dt:.2f} KB/s={(rx_bytes - rx0) / 1024 / max(dt, 1e-3):.0f}")

        if args.audit:
            await wait_prompt(10, 0.2)
            cmds = [__import__("codecs").decode(x, "unicode_escape") for x in args.audit_cmds.split("|") if x] or [
                "display clock\r",
                "disX\x08play ver\t\r",
                "dis ip int br\t\r",
                "\r",
                "display clock\r",
                "\x1b[A\r",
                "\x1b[A\x1b[A\x1b[B\r",
                "system-view\r",
                "sysname r1\r",
                "quit\r",
            ]
            for cmd in cmds:
                await send(cmd)
                await asyncio.sleep(0.2)
                await wait_prompt(10, 0.25)
                if "More" in buf[-40:]:
                    await send(" ")
            await asyncio.sleep(0.5)

        await ws.send(json.dumps({"type": "close"}))
        await asyncio.sleep(0.3)
        rt.cancel()

    time.sleep(0.8)
    if args.audit:
        print("--- audit.jsonl commands for session ---")
        try:
            with open(args.audit_file, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("event") == "command" and o.get("session_id") == sid:
                        print(repr(o.get("command")), o.get("source"))
        except OSError as exc:
            print("cannot read audit:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
