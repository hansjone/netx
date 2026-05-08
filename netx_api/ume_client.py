from __future__ import annotations

from dataclasses import dataclass
import json
from threading import Lock
from time import time
from typing import Any, Callable

import httpx

from .config import settings


def _rstrip_slash(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


@dataclass
class RequestDiagnostics:
    method: str
    path: str
    status_code: int
    latency_ms: int
    retry_count: int = 0
    error_code: str = ""


class UMEClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_tls: bool | None = None,
        timeout_s: float | None = None,
        auth_header: str | None = None,
        content_type: str | None = None,
        token_ttl_s: int | None = None,
        token_refresh_skew_s: int | None = None,
        token_path: str | None = None,
        token_handshake_path: str | None = None,
        token_logout_path: str | None = None,
        ne_path: str | None = None,
        alarms_path: str | None = None,
        token_loader: Callable[[], tuple[str, float] | None] | None = None,
        token_saver: Callable[[str, float], None] | None = None,
        token_clearer: Callable[[], None] | None = None,
        lock_acquirer: Callable[[], bool] | None = None,
        lock_releaser: Callable[[], None] | None = None,
        token_waiter: Callable[[float], tuple[str, float] | None] | None = None,
    ) -> None:
        self.base_url = _rstrip_slash(base_url if base_url is not None else settings.ume_base_url)
        self.username = str(username if username is not None else settings.ume_username)
        self.password = str(password if password is not None else settings.ume_password)
        self.verify_tls = bool(settings.ume_verify_tls if verify_tls is None else verify_tls)
        self.timeout_s = max(3.0, float(settings.ume_timeout_s if timeout_s is None else timeout_s))
        self.auth_header = str(auth_header if auth_header is not None else settings.ume_auth_header).strip() or "accessToken"
        self.content_type = str(content_type if content_type is not None else settings.ume_content_type).strip()
        self.token_ttl_s = max(60, int(settings.ume_token_ttl_s if token_ttl_s is None else token_ttl_s))
        self.token_refresh_skew_s = max(
            5, int(settings.ume_token_refresh_skew_s if token_refresh_skew_s is None else token_refresh_skew_s)
        )
        self.token_path = str(token_path if token_path is not None else settings.ume_token_path).strip()
        self.token_handshake_path = str(
            token_handshake_path if token_handshake_path is not None else settings.ume_token_handshake_path
        ).strip()
        self.token_logout_path = str(token_logout_path if token_logout_path is not None else settings.ume_token_logout_path).strip()
        self.ne_path = str(ne_path if ne_path is not None else settings.ume_ne_path).strip()
        self.alarms_path = str(alarms_path if alarms_path is not None else settings.ume_alarms_path).strip()
        self._token_loader = token_loader
        self._token_saver = token_saver
        self._token_clearer = token_clearer
        self._lock_acquirer = lock_acquirer
        self._lock_releaser = lock_releaser
        self._token_waiter = token_waiter

        self._token_lock = Lock()
        self._token_value: str = ""
        self._token_expires_at: float = 0.0
        self._last_token_source: str = "memory"
        self._last_store_sync_changed: bool = False

    def _sync_token_from_store(self) -> None:
        self._last_store_sync_changed = False
        if self._token_loader is None:
            return
        try:
            loaded = self._token_loader()
        except Exception:
            return
        if not loaded:
            return
        token, exp = loaded
        token = str(token or "").strip()
        if not token:
            return
        if exp > float(self._token_expires_at):
            self._token_value = token
            self._token_expires_at = float(exp)
            self._last_token_source = "db"
            self._last_store_sync_changed = True

    def _persist_token_to_store(self) -> None:
        if self._token_saver is None:
            return
        try:
            self._token_saver(self._token_value, self._token_expires_at)
        except Exception:
            pass

    def _clear_token_in_store(self) -> None:
        if self._token_clearer is None:
            return
        try:
            self._token_clearer()
        except Exception:
            pass

    def token_status(self) -> dict[str, Any]:
        self._sync_token_from_store()
        now = time()
        token = self._token_value.strip()
        has_token = bool(token)
        expires_in_s = int(max(0, self._token_expires_at - now)) if has_token else 0
        token_preview = ""
        if token:
            token_preview = f"{token[:8]}...{token[-4:]}" if len(token) > 16 else token
        return {
            "has_token": has_token,
            "expires_in_s": expires_in_s,
            "expires_at_epoch_s": int(self._token_expires_at) if has_token else 0,
            "auth_header": self.auth_header,
            "token_preview": token_preview,
            "source": str(self._last_token_source or "memory"),
            "store_synced": bool(self._last_store_sync_changed),
        }

    def _assert_ready(self) -> None:
        if not self.base_url:
            raise RuntimeError("ume_base_url_required")
        if not self.username:
            raise RuntimeError("ume_username_required")
        if not self.password:
            raise RuntimeError("ume_password_required")

    def _build_url(self, path: str) -> str:
        p = str(path or "").strip()
        if not p:
            raise RuntimeError("ume_path_required")
        if p.startswith("http://") or p.startswith("https://"):
            return p
        if not p.startswith("/"):
            p = "/" + p
        return f"{self.base_url}{p}"

    def _headers(self, *, include_token: bool = True) -> dict[str, str]:
        headers = {
            "content-type": self.content_type,
        }
        if include_token:
            token = self._token_value.strip()
            if token:
                headers[self.auth_header] = token
        return headers

    def _client(self) -> httpx.Client:
        # Force HTTP/1.1 (disable HTTP/2) due to UME server compatibility issues.
        # httpx defaults to HTTP/1.1 unless http2=True, but some environments still
        # negotiate/behave unexpectedly; be explicit here.
        return httpx.Client(verify=self.verify_tls, timeout=self.timeout_s, http2=False)

    def _extract_token_and_ttl(self, payload: dict[str, Any]) -> tuple[str, int | None]:
        token = ""
        ttl: int | None = None

        def walk(node: Any) -> None:
            nonlocal token, ttl
            if isinstance(node, dict):
                for k, v in node.items():
                    key = str(k).lower()
                    if not token and key in {"accesstoken", "access_token", "token"} and isinstance(v, str):
                        token = v.strip()
                    if ttl is None and key in {"expires", "expiresin", "expires_in", "ttl"}:
                        try:
                            ttl = int(v)
                        except Exception:
                            pass
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return token, ttl

    def login(self, *, force: bool = False) -> str:
        self._assert_ready()
        self._sync_token_from_store()
        now = time()
        if not force and self._token_value and now < (self._token_expires_at - self.token_refresh_skew_s):
            return self._token_value

        with self._token_lock:
            now = time()
            if not force and self._token_value and now < (self._token_expires_at - self.token_refresh_skew_s):
                return self._token_value

            # Cross-process singleflight: if another process is refreshing, wait for DB update.
            min_exp = float(self._token_expires_at)
            if self._lock_acquirer is not None:
                acquired = False
                try:
                    acquired = bool(self._lock_acquirer())
                except Exception:
                    acquired = False
                if not acquired:
                    if self._token_waiter is not None:
                        waited = self._token_waiter(min_exp)
                        if waited:
                            self._sync_token_from_store()
                            now = time()
                            if self._token_value and now < (self._token_expires_at - self.token_refresh_skew_s):
                                return self._token_value

            url = self._build_url(self.token_path)
            payload = {"login-info": {"user-name": self.username, "value": self.password}}
            try:
                with self._client() as client:
                    resp = client.post(url, json=payload, headers=self._headers(include_token=False))
                if not resp.is_success:
                    raise RuntimeError(f"ume_login_failed:{resp.status_code}:{resp.text[:240]}")
                data = _coerce_dict(resp.json())
            except Exception as exc:
                if self._lock_releaser is not None:
                    try:
                        self._lock_releaser()
                    except Exception:
                        pass
                raise RuntimeError(f"ume_login_failed:{str(exc)[:240]}") from exc

            token, ttl = self._extract_token_and_ttl(data)
            if not token:
                if self._lock_releaser is not None:
                    try:
                        self._lock_releaser()
                    except Exception:
                        pass
                raise RuntimeError("ume_login_failed:missing_access_token")
            use_ttl = max(60, int(ttl)) if ttl is not None else self.token_ttl_s
            self._token_value = token
            self._token_expires_at = time() + use_ttl
            self._last_token_source = "memory"
            self._persist_token_to_store()
            if self._lock_releaser is not None:
                try:
                    self._lock_releaser()
                except Exception:
                    pass
            return self._token_value

    def renew_token(self) -> str:
        self._assert_ready()
        token = self._token_value.strip()
        if not token:
            return self.login(force=True)
        with self._token_lock:
            token = self._token_value.strip()
            if not token:
                return self.login(force=True)
            url = self._build_url(self.token_handshake_path)
            try:
                with self._client() as client:
                    resp = client.post(url, headers=self._headers(include_token=True))
                if not resp.is_success:
                    # handshake may fail if token expired; fallback to full login
                    return self.login(force=True)
                # Per UME guide, oauth_handshake may return no body; treat HTTP 2xx as success.
                next_token = ""
                ttl: int | None = None
                try:
                    text = (resp.text or "").strip()
                    if text:
                        data = _coerce_dict(resp.json())
                        next_token, ttl = self._extract_token_and_ttl(data)
                except Exception:
                    # ignore json parse errors, success is based on status code
                    next_token = ""
                    ttl = None
                if next_token:
                    self._token_value = next_token
                # If handshake doesn't provide ttl, fall back to configured ttl.
                use_ttl = max(60, int(ttl)) if ttl is not None else self.token_ttl_s
                self._token_expires_at = time() + use_ttl
                self._last_token_source = "memory"
                self._persist_token_to_store()
                return self._token_value
            except Exception:
                return self.login(force=True)

    def logout_token(self) -> bool:
        token = self._token_value.strip()
        if not token:
            return True
        url = self._build_url(self.token_logout_path)
        try:
            with self._client() as client:
                resp = client.delete(url, headers=self._headers(include_token=True))
            if resp.status_code in (401, 403):
                # Token already invalid/expired on server side can be treated as logged out.
                self._token_value = ""
                self._token_expires_at = 0.0
                self._last_token_source = "memory"
                self._clear_token_in_store()
                return True
            if not resp.is_success:
                return False
            self._token_value = ""
            self._token_expires_at = 0.0
            self._last_token_source = "memory"
            self._clear_token_in_store()
            return True
        except Exception:
            return False

    def refresh_if_needed(self) -> str:
        self._sync_token_from_store()
        now = time()
        if self._token_value and now < (self._token_expires_at - self.token_refresh_skew_s):
            return self._token_value
        if self._token_value:
            return self.renew_token()
        return self.login(force=False)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], RequestDiagnostics]:
        self.refresh_if_needed()
        url = self._build_url(path)
        m = str(method or "GET").upper()
        retry_count = 0
        t0 = time()
        try:
            with self._client() as client:
                resp = client.request(m, url, params=params, json=body, headers=self._headers(include_token=True))
            if resp.status_code in (401, 403):
                retry_count = 1
                self.login(force=True)
                with self._client() as client:
                    resp = client.request(m, url, params=params, json=body, headers=self._headers(include_token=True))
            if not resp.is_success:
                diag = RequestDiagnostics(
                    method=m,
                    path=path,
                    status_code=int(resp.status_code),
                    latency_ms=int((time() - t0) * 1000),
                    retry_count=retry_count,
                    error_code=f"http_{int(resp.status_code)}",
                )
                raise RuntimeError(f"ume_request_failed:{resp.status_code}:{resp.text[:240]}")
            data = _coerce_dict(resp.json())
            diag = RequestDiagnostics(
                method=m,
                path=path,
                status_code=int(resp.status_code),
                latency_ms=int((time() - t0) * 1000),
                retry_count=retry_count,
            )
            return data, diag
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"ume_request_failed:{str(exc)[:240]}") from exc

    def _extract_named_list(self, payload: dict[str, Any], keys: list[str]) -> list[dict[str, Any]]:
        target_keys = {str(k).lower() for k in keys}
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_unique(item: dict[str, Any]) -> None:
            try:
                mark = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                mark = str(item)
            if mark in seen:
                return
            seen.add(mark)
            found.append(item)

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if str(k).lower() in target_keys:
                        if isinstance(v, list):
                            for it in v:
                                if isinstance(it, dict):
                                    add_unique(it)
                        elif isinstance(v, dict):
                            # Common RESTCONF wrappers are container dicts, e.g.
                            # network-elements -> network-element[] / alarm-list -> alarm[].
                            # Prefer unwrapping nested list payloads before falling back.
                            nested_collected = False
                            for nested_v in v.values():
                                if isinstance(nested_v, list):
                                    for it in nested_v:
                                        if isinstance(it, dict):
                                            add_unique(it)
                                            nested_collected = True
                            if not nested_collected:
                                add_unique(v)
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return found

    def get_network_elements(self) -> tuple[list[dict[str, Any]], RequestDiagnostics]:
        data, diag = self.request_json("GET", self.ne_path)
        rows = self._extract_named_list(data, ["network-elements", "network-element", "ne", "network_elements"])
        if rows:
            return rows, diag
        # fallback: some responses may directly return list-like map at top-level
        for v in data.values():
            lst = _coerce_list(v)
            if lst and isinstance(lst[0], dict):
                return [x for x in lst if isinstance(x, dict)], diag
        return [], diag

    def get_alarms(
        self,
        *,
        is_uncleared: bool,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple[list[dict[str, Any]], RequestDiagnostics]:
        limit_max = int(getattr(settings, "ume_limit_max", 5000) or 5000)
        limit_max = max(1, limit_max)
        page_size = int(limit or settings.ume_page_size or 1000)
        page_size = max(1, min(page_size, limit_max))
        params: dict[str, Any] = {
            "is-uncleared": "true" if is_uncleared else "false",
            "limit": page_size,
        }
        if offset is not None and int(offset) >= 0:
            params["offset"] = int(offset)
        data, diag = self.request_json("GET", self.alarms_path, params=params)
        rows = self._extract_named_list(data, ["alarm-list", "alarm"])
        return rows, diag
