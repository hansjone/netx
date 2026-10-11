#!/usr/bin/env python3
"""Keystroke-level WebCRT audit scenarios against a live device.

Every key is sent as its own stdin frame (~40ms apart, like a person typing) and the
command that landed in audit.jsonl for the session is compared with what the device
actually executed.

    python scripts/e2e_webcrt_input_scenarios.py --ne-id <id> --vendor huawei
    python scripts/e2e_webcrt_input_scenarios.py --ne-id <id> --vendor cisco
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

import httpx
import websockets

BASE = os.environ.get("NETX_BASE", "http://127.0.0.1:8890").rstrip("/")
USER = os.environ.get("NETX_USER", "admin")
PWD = os.environ.get("NETX_PASSWORD", "Admin@1234")

LEFT, RIGHT, UP, DOWN = "\x1b[D", "\x1b[C", "\x1b[A", "\x1b[B"
HOME, END, DEL, BS, TAB, CR, CTRL_C = "\x1b[H", "\x1b[F", "\x1b[3~", "\x08", "\t", "\r", "\x03"


def keys(*parts: str | list[str]) -> list[str]:
    """Flatten: a plain str is typed char by char, escape sequences stay whole."""
    out: list[str] = []
    for p in parts:
        if isinstance(p, list):
            out.extend(p)
        elif p.startswith("\x1b") or p in (BS, TAB, CR, CTRL_C):
            out.append(p)
        else:
            out.extend(list(p))
    return out


def scenarios(vendor: str) -> list[tuple[str, list[str], "str | list[str]"]]:
    """(name, keystrokes, expected command text after the prompt)."""
    if vendor == "cisco":
        full, partial, tabbed, other = "show clock", "show cl", "sh clo", "show version"
        wrong = "show clxock"
    else:
        full, partial, tabbed, other = "display clock", "display cl", "display clo", "display version"
        wrong = "display clxock"
    word = full.split()[-1]  # clock
    return [
        ("plain", keys(full, CR), full),
        ("backspace fix", keys(full[:-3] + "x", BS, full[-3:], CR), full),
        ("tab completion", keys(tabbed, TAB, CR), "sh clock" if vendor == "cisco" else full),
        ("left-arrow insert mid-line", keys(full.replace("clock", "cock"), [LEFT] * 3, "l", CR), full),
        ("left-arrow + backspace delete", keys(wrong, [LEFT] * 3, BS, CR), full),
        ("left-arrow + Delete key", keys(wrong, [LEFT] * 4, DEL, CR), full),
        ("left-arrow replace char", keys(full.replace("clock", "clxck"), [LEFT] * 2, BS, "o", CR), full),
        ("home + insert first char", keys(full[1:], HOME, full[0], END, CR), full),
        ("right-arrow after left", keys(full, LEFT, LEFT, RIGHT, RIGHT, CR), full),
        ("up-arrow recall", keys(UP, CR), full),
        ("up-arrow recall then edit tail", keys(UP, [BS] * len(word), other.split()[-1], CR), other),
        ("up up down recall", keys(UP, UP, DOWN, CR), other),
        ("recall + left-arrow delete+insert", keys(UP, [LEFT] * 2, BS, "c", CR), other[:-3] + "con"),
        ("ctrl-c (device decides if line clears)", keys("garbage", CTRL_C, full, CR), ("garbage" + full) if vendor == "huawei" else full),
        ("empty enter x2 (not audited)", keys(CR, CR), ""),
        ("run then recall and run again", keys(full, CR, UP, CR), [full, full]),
    ]


async def run(args: argparse.Namespace) -> int:
    c = httpx.Client(base_url=BASE, timeout=60)
    tok = c.post("/v1/auth/login", json={"username": USER, "password": PWD}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    sid = c.post("/v1/webcrt/sessions", headers=h, json={"ne_id": args.ne_id, "cols": 160, "rows": 50}).json()["session_id"]
    ticket = c.post("/v1/webcrt/ws-ticket", headers=h).json()["ticket"]
    url = f"{BASE.replace('http', 'ws', 1)}/v1/webcrt/sessions/{sid}/ws?ws_ticket={ticket}"
    prompt_re = re.compile(r"(?:<[^>\r\n]+>|\[[^\]\r\n]+\]|[\w.\-]+(?:\([^)]+\))*[#>])\s*$")
    results: list[tuple[str, str | None, list[str]]] = []

    async with websockets.connect(url, max_size=None) as ws:
        buf = ""
        last_rx = 0.0

        async def reader() -> None:
            nonlocal buf, last_rx
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        buf += raw.decode("utf-8", "replace")
                        last_rx = time.perf_counter()
            except Exception:
                pass

        rt = asyncio.create_task(reader())

        def at_prompt() -> bool:
            plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf).replace("\r", "\n")
            tail = plain.rstrip(" ").split("\n")[-1]
            return bool(prompt_re.search(tail)) and len(tail) < 80

        async def settle(timeout: float = 20.0, quiet: float = 0.6) -> None:
            end = time.perf_counter() + timeout
            while time.perf_counter() < end:
                if at_prompt() and time.perf_counter() - last_rx >= quiet:
                    return
                await asyncio.sleep(0.02)

        async def send(data: str) -> None:
            await ws.send(json.dumps({"type": "stdin", "data": data}))

        await settle(120, 1.0)
        if not at_prompt():
            await send("\r")
            await settle(15, 1.0)
        sel = [x.strip() for x in args.only.split(",") if x.strip()]
        for name, ks, expected in scenarios(args.vendor):
            if sel and not name.startswith(tuple(sel)):
                continue
            before = len(read_cmds(sid, args.audit_file))
            for k in ks:
                await send(k)
                await asyncio.sleep(args.key_gap)
            # Wait for the device (slow VMs echo seconds late), not a fixed delay: first for
            # output after the last key, then until the expected audit rows exist.
            sent_at = time.perf_counter()
            while last_rx < sent_at and time.perf_counter() - sent_at < 15:
                await asyncio.sleep(0.02)
            await settle()
            want = len(expected) if isinstance(expected, list) else (0 if expected == "" else 1)
            waited = time.perf_counter()
            while time.perf_counter() - waited < (2.0 if want == 0 else 15.0):
                if len(read_cmds(sid, args.audit_file)) - before >= want:
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.5)  # Tab lines settle ~1.2s; also catches stray extra rows
            got = read_cmds(sid, args.audit_file)[before:]
            results.append((name, expected, got))
        await ws.send(json.dumps({"type": "close"}))
        await asyncio.sleep(0.5)
        rt.cancel()

    bad = 0
    print(f"{'scenario':38} {'result':6} recorded")
    for name, expected, got in results:
        tails = [re.sub(r"^(?:<[^>]*>|\[[^\]]*\]|[\w.\-]+(?:\([^)]*\))*[#>])\s*", "", g) for g in got]
        if isinstance(expected, list):
            ok = tails == expected
        elif expected == "":
            ok = not got
        else:
            ok = tails == [expected]
        bad += 0 if ok else 1
        print(f"{name:38} {'OK' if ok else 'FAIL':6} {got!r}" + ("" if ok else f"   expected {expected!r}"))
    print(f"\n{len(results) - bad}/{len(results)} scenarios correct")
    return 1 if bad else 0


def read_cmds(sid: str, path: str) -> list[str]:
    out: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if '"event": "command"' not in line:
                    continue
                o = json.loads(line)
                if o.get("session_id") == sid:
                    out.append(str(o.get("command") or ""))
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ne-id", required=True)
    ap.add_argument("--vendor", choices=["huawei", "cisco"], required=True)
    ap.add_argument("--only", default="", help="comma-separated scenario name prefixes")
    ap.add_argument("--key-gap", type=float, default=0.04, help="seconds between keystrokes")
    ap.add_argument("--audit-file", default=os.path.join("data", "webcrt", "audit.jsonl"))
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
