"""Resolve client IP behind reverse proxies (Vite / nginx / Caddy).

Only honors X-Forwarded-For / X-Real-IP / CF-Connecting-IP when the direct peer
is in NETX_TRUSTED_PROXY_IPS (default loopback). Otherwise the peer address is
used — prevents header spoofing from the public internet.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Mapping

from starlette.requests import Request
from starlette.websockets import WebSocket

from .config import settings

_IP_RE = re.compile(
    r"^(?:"
    r"(?:\d{1,3}\.){3}\d{1,3}"  # IPv4
    r"|"
    r"\[[0-9a-fA-F:]+\]"  # [IPv6]
    r"|"
    r"[0-9a-fA-F:]+"  # IPv6 bare
    r")$"
)


def _trusted_peers() -> set[str]:
    raw = str(getattr(settings, "trusted_proxy_ips", "") or "")
    out: set[str] = set()
    for part in raw.split(","):
        p = part.strip()
        if p:
            out.add(p)
    # Always treat classic loopback as trusted for local Vite/nginx.
    out.update({"127.0.0.1", "::1", "localhost"})
    return out


def _looks_like_ip(value: str) -> bool:
    cand = str(value or "").strip()
    if not cand or not _IP_RE.match(cand):
        return False
    if cand.startswith("[") and cand.endswith("]"):
        cand = cand[1:-1]
    try:
        ipaddress.ip_address(cand)
        return True
    except ValueError:
        return False


def _normalize_ip(value: str) -> str:
    cand = str(value or "").strip()
    if cand.startswith("[") and cand.endswith("]"):
        cand = cand[1:-1]
    return cand[:128]


def _header_map(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    # Starlette Headers is case-insensitive; normalize keys for .get
    return {str(k).lower(): str(v) for k, v in headers.items()}


def resolve_client_ip_from(peer: str, headers: Mapping[str, str] | None = None) -> str:
    """Pick the best client IP given the TCP peer and request headers."""
    peer_ip = _normalize_ip(peer)
    hdrs = _header_map(headers)
    trusted = _trusted_peers()
    peer_trusted = peer_ip in trusted or peer_ip.lower() in {x.lower() for x in trusted}

    if not peer_trusted:
        return peer_ip or ""

    # Cloudflare (single value)
    cf = (hdrs.get("cf-connecting-ip") or "").strip()
    if _looks_like_ip(cf):
        return _normalize_ip(cf)

    # nginx often sets this to the original client
    real = (hdrs.get("x-real-ip") or "").strip()
    if _looks_like_ip(real):
        return _normalize_ip(real)

    # X-Forwarded-For: client, proxy1, proxy2 — take first public-looking hop
    xff = hdrs.get("x-forwarded-for") or ""
    for part in xff.split(","):
        cand = part.strip()
        if _looks_like_ip(cand):
            return _normalize_ip(cand)

    return peer_ip or ""


def resolve_client_ip(request: Request) -> str:
    peer = str(request.client.host if request.client else "") or ""
    return resolve_client_ip_from(peer, request.headers)


def resolve_websocket_client_ip(websocket: WebSocket) -> str:
    peer = str(websocket.client.host if websocket.client else "") or ""
    return resolve_client_ip_from(peer, websocket.headers)
