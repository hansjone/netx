from __future__ import annotations

from typing import Any

import httpx

from .config import settings


def _analyze_httpx_timeout() -> httpx.Timeout:
    read_s = max(30.0, float(settings.oclaw_analyze_read_timeout_sec))
    connect_s = max(3.0, float(settings.oclaw_connect_timeout_sec))
    return httpx.Timeout(
        connect=connect_s,
        read=read_s,
        write=min(120.0, read_s),
        pool=connect_s,
    )


def analyze_with_oclaw(payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"content-type": "application/json", "accept": "application/json"}
    token = str(settings.oclaw_analyze_token or "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=_analyze_httpx_timeout()) as client:
        resp = client.post(settings.oclaw_analyze_url, json=payload, headers=headers)
        text = resp.text
        if not resp.is_success:
            raise RuntimeError(f"oclaw analyze failed: {resp.status_code} {text[:300]}")
        if not text:
            return {}
        return resp.json()


def health_with_oclaw() -> dict[str, Any]:
    headers = {"accept": "application/json"}
    token = str(settings.oclaw_analyze_token or "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"
    health_s = max(3.0, float(settings.oclaw_health_timeout_sec))
    with httpx.Client(timeout=health_s) as client:
        resp = client.get(settings.oclaw_health_url, headers=headers)
        status = int(resp.status_code)
        text = resp.text
        if not resp.is_success:
            raise RuntimeError(f"oclaw health failed: {status} {text[:200]}")
        if not text:
            return {"status_code": status, "data": {}}
        return {"status_code": status, "data": resp.json()}
