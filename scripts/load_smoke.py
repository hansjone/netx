"""HTTP concurrency smoke / load probe for a running netx API.

Does not SSH to devices. Measures API responsiveness, DB readiness under
concurrent reads, and /metrics pool+CLI budget snapshots.

Usage (API already running):
  .\\.venv\\Scripts\\python.exe scripts\\load_smoke.py
  .\\.venv\\Scripts\\python.exe scripts\\load_smoke.py --base http://127.0.0.1:8890 --workers 40 --seconds 20
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Sample:
    name: str
    ok: int = 0
    fail: int = 0
    statuses: dict[int, int] = field(default_factory=dict)
    lat_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, status: int, ms: float, err: str = "") -> None:
        self.lat_ms.append(ms)
        self.statuses[status] = self.statuses.get(status, 0) + 1
        # Windows ephemeral-port exhaustion under aggressive client concurrency.
        if status == 0 and ("10048" in err or "Address already in use" in err or "只允许使用一次" in err):
            self.client_port_exhaust = getattr(self, "client_port_exhaust", 0) + 1
            if err and len(self.errors) < 8:
                self.errors.append(err[:160])
            return
        if 200 <= status < 400:
            self.ok += 1
        else:
            self.fail += 1
            if err and len(self.errors) < 8:
                self.errors.append(err[:160])

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.lat_ms)
        def pct(p: float) -> float | None:
            if not lat:
                return None
            idx = min(len(lat) - 1, max(0, int(round((p / 100.0) * (len(lat) - 1)))))
            return round(lat[idx], 1)

        return {
            "name": self.name,
            "ok": self.ok,
            "fail": self.fail,
            "client_port_exhaust": int(getattr(self, "client_port_exhaust", 0)),
            "total": self.ok + self.fail + int(getattr(self, "client_port_exhaust", 0)),
            "rps": None,
            "p50_ms": pct(50),
            "p95_ms": pct(95),
            "p99_ms": pct(99),
            "avg_ms": round(statistics.fmean(lat), 1) if lat else None,
            "statuses": dict(sorted(self.statuses.items())),
            "errors": list(self.errors),
        }


def http_json(url: str, *, method: str = "GET", data: dict | None = None, token: str = "", timeout: float = 15.0) -> tuple[int, Any, float]:
    body = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = (time.perf_counter() - t0) * 1000
            try:
                payload = json.loads(raw.decode("utf-8") or "null")
            except Exception:
                payload = raw.decode("utf-8", errors="replace")[:200]
            return int(resp.status), payload, ms
    except urllib.error.HTTPError as exc:
        ms = (time.perf_counter() - t0) * 1000
        raw = exc.read().decode("utf-8", errors="replace")[:200]
        return int(exc.code), raw, ms
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return 0, str(exc)[:200], ms


def snapshot_metrics(base: str) -> dict[str, Any]:
    code, payload, ms = http_json(f"{base}/metrics/json")
    return {"http_status": code, "latency_ms": round(ms, 1), "body": payload if code == 200 else {"error": payload}}


def login(base: str, username: str, password: str) -> str:
    code, payload, _ = http_json(
        f"{base}/v1/auth/login",
        method="POST",
        data={"username": username, "password": password},
    )
    if code != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"login_failed status={code} body={payload}")
    token = str(payload.get("access_token") or payload.get("token") or "")
    if not token:
        raise RuntimeError(f"login_no_token body={payload}")
    return token


def run_wave(
    *,
    name: str,
    urls: list[tuple[str, str]],
    workers: int,
    seconds: float,
    token: str = "",
) -> Sample:
    sample = Sample(name=name)
    stop_at = time.perf_counter() + max(1.0, seconds)
    lock = threading.Lock()
    idx = 0

    def one() -> None:
        nonlocal idx
        while time.perf_counter() < stop_at:
            with lock:
                i = idx
                idx += 1
            path, method = urls[i % len(urls)]
            code, body, ms = http_json(path, method=method, token=token, timeout=20.0)
            err = "" if 200 <= code < 400 else str(body)
            sample.add(code, ms, err)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(one) for _ in range(max(1, workers))]
        for fut in as_completed(futs):
            fut.result()
    summary = sample.summary()
    elapsed = max(0.001, seconds)
    summary["rps"] = round((sample.ok + sample.fail) / elapsed, 1)
    sample.rps = summary["rps"]  # type: ignore[attr-defined]
    return sample


def main() -> int:
    ap = argparse.ArgumentParser(description="netx API concurrency smoke probe")
    ap.add_argument("--base", default="http://127.0.0.1:8890")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin123")
    ap.add_argument("--token", default="", help="Bearer token; default tries data/auth/mcp_token then login")
    ap.add_argument("--out", default="scripts/.run/load_smoke_report.json")
    args = ap.parse_args()
    base = str(args.base).rstrip("/")

    code, health, _ = http_json(f"{base}/health")
    if code != 200:
        print(f"[ERR] API not healthy at {base}/health -> {code} {health}", file=sys.stderr)
        return 2

    before = snapshot_metrics(base)
    ready_code, ready_body, ready_ms = http_json(f"{base}/health/ready")

    public = run_wave(
        name="public_health_metrics",
        urls=[
            (f"{base}/health", "GET"),
            (f"{base}/health/live", "GET"),
            (f"{base}/health/ready", "GET"),
            (f"{base}/metrics", "GET"),
            (f"{base}/metrics/json", "GET"),
        ],
        workers=args.workers,
        seconds=args.seconds,
    )

    auth_sample: dict[str, Any] | None = None
    auth_err = ""
    token = str(args.token or "").strip()
    if not token:
        mcp_path = Path("data/auth/mcp_token")
        if mcp_path.is_file():
            token = mcp_path.read_text(encoding="utf-8").strip()
    if not token:
        try:
            token = login(base, args.user, args.password)
        except Exception as exc:  # noqa: BLE001
            auth_err = str(exc)[:240]
            token = ""
    try:
        if not token:
            raise RuntimeError(auth_err or "no_token")
        auth = run_wave(
            name="authed_read_apis",
            urls=[
                (f"{base}/v1/integrations/status", "GET"),
                (f"{base}/v1/managed-ne?limit=50", "GET"),
                (f"{base}/v1/topology/views", "GET"),
                (f"{base}/v1/topology/fabric/summary", "GET"),
                (f"{base}/v1/ume/inventory/ne?limit=50", "GET"),
                (f"{base}/v1/port-traffic/devices", "GET"),
                (f"{base}/v1/alarms?limit=50", "GET"),
            ],
            workers=max(8, args.workers // 2),
            seconds=max(8.0, args.seconds * 0.7),
            token=token,
        )
        auth_sample = auth.summary()
        auth_sample["rps"] = round((auth.ok + auth.fail) / max(8.0, args.seconds * 0.7), 1)
    except Exception as exc:  # noqa: BLE001
        auth_err = str(exc)[:240]

    after = snapshot_metrics(base)
    report = {
        "base": base,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "workers": args.workers,
            "seconds": args.seconds,
            "user": args.user,
        },
        "precheck": {
            "health": health,
            "ready_status": ready_code,
            "ready_latency_ms": round(ready_ms, 1),
            "ready": ready_body if ready_code == 200 else {"error": ready_body},
        },
        "metrics_before": before,
        "public_wave": public.summary() | {"rps": round((public.ok + public.fail) / max(1.0, args.seconds), 1)},
        "auth_wave": auth_sample,
        "auth_error": auth_err,
        "metrics_after": after,
    }

    # Simple verdicts
    verdicts: list[str] = []
    pub = report["public_wave"]
    if pub["fail"] == 0 and (pub.get("p95_ms") or 0) < 500:
        verdicts.append("public endpoints: PASS (no server failures, p95 < 500ms)")
    elif pub["fail"] == 0:
        verdicts.append(f"public endpoints: WARN (ok but p95={pub.get('p95_ms')}ms)")
    else:
        verdicts.append(f"public endpoints: FAIL ({pub['fail']} server errors)")
    if int(pub.get("client_port_exhaust") or 0) > 0:
        verdicts.append(
            f"public client ports: EXHAUST ({pub['client_port_exhaust']} WinError 10048) — lower --workers on Windows"
        )

    if auth_sample:
        if auth_sample["fail"] == 0 and (auth_sample.get("p95_ms") or 0) < 1000:
            verdicts.append("authed reads: PASS")
        elif auth_sample["fail"] == 0:
            verdicts.append(f"authed reads: WARN (p95={auth_sample.get('p95_ms')}ms)")
        else:
            verdicts.append(f"authed reads: FAIL ({auth_sample['fail']} server errors)")
        if int(auth_sample.get("client_port_exhaust") or 0) > 0:
            verdicts.append(
                f"authed client ports: EXHAUST ({auth_sample['client_port_exhaust']} WinError 10048)"
            )
    else:
        verdicts.append(f"authed reads: SKIP ({auth_err or 'no token'})")

    pool_after = ((after.get("body") or {}).get("db_pool") or {}) if isinstance(after.get("body"), dict) else {}
    checked = pool_after.get("checked_out")
    size = pool_after.get("size") or pool_after.get("pool_size_cfg")
    if checked is not None and size is not None and int(size) > 0:
        util = int(checked) / max(1, int(size))
        if util < 0.7:
            verdicts.append(f"db pool after load: OK (checked_out={checked}/{size})")
        else:
            verdicts.append(f"db pool after load: HOT (checked_out={checked}/{size})")

    report["verdicts"] = verdicts

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"verdicts": verdicts, "public": report["public_wave"], "auth": auth_sample, "report": str(out)}, ensure_ascii=False, indent=2))
    bad = [v for v in verdicts if ": FAIL" in v or ": HOT" in v]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
