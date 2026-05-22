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
    marker: str = ""
    is_end_of_reply: bool | None = None


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
        notification_establish_path: str | None = None,
        notification_delete_path: str | None = None,
        notification_topic: str | None = None,
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
        self.notification_establish_path = str(
            notification_establish_path
            if notification_establish_path is not None
            else settings.ume_notification_establish_path
        ).strip()
        self.notification_delete_path = str(
            notification_delete_path
            if notification_delete_path is not None
            else settings.ume_notification_delete_path
        ).strip()
        self.notification_topic = str(
            notification_topic if notification_topic is not None else settings.ume_notification_topic
        ).strip() or "ALARM"
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
            if self._token_value.strip():
                self._token_value = ""
                self._token_expires_at = 0.0
                self._last_token_source = "memory"
                self._last_store_sync_changed = True
            return
        token, exp = loaded
        token = str(token or "").strip()
        if not token:
            if self._token_value.strip():
                self._token_value = ""
                self._token_expires_at = 0.0
                self._last_token_source = "memory"
                self._last_store_sync_changed = True
            return
        exp_f = float(exp)
        mem_exp = float(self._token_expires_at)
        mem_tok = self._token_value.strip()
        # Adopt DB when we have no local token, DB has newer expiry, or token string changed.
        # If DB expiry is missing (0) but we already have the same token with a positive local expiry,
        # keep local expiry (avoids token_status showing disconnected after restart when DB row is incomplete).
        adopt = False
        if not mem_tok:
            adopt = True
        elif token != mem_tok:
            adopt = True
        elif exp_f > mem_exp:
            adopt = True
        elif exp_f <= 0 and not (token == mem_tok and mem_exp > 0):
            adopt = True
        if adopt:
            if token != mem_tok or exp_f != mem_exp:
                self._last_store_sync_changed = True
            self._token_value = token
            self._token_expires_at = exp_f
            self._last_token_source = "db"

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
        # Use explicit HTTPTransport to keep behavior consistent with onsite validation.
        # In this mode, requests run over HTTP/1.1 and avoid HTTP/2 negotiation issues.
        transport = httpx.HTTPTransport(verify=self.verify_tls, http2=False)
        return httpx.Client(transport=transport, timeout=self.timeout_s)
        

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
        if not self._token_value.strip():
            return self.login(force=True)

        do_login = False
        renewed_value = ""
        with self._token_lock:
            token = self._token_value.strip()
            if not token:
                do_login = True
            else:
                url = self._build_url(self.token_handshake_path)
                try:
                    with self._client() as client:
                        resp = client.post(url, headers=self._headers(include_token=True))
                    if not resp.is_success:
                        do_login = True
                    else:
                        next_token = ""
                        ttl: int | None = None
                        try:
                            text = (resp.text or "").strip()
                            if text:
                                data = _coerce_dict(resp.json())
                                next_token, ttl = self._extract_token_and_ttl(data)
                        except Exception:
                            next_token = ""
                            ttl = None
                        if next_token:
                            self._token_value = next_token
                        use_ttl = max(60, int(ttl)) if ttl is not None else self.token_ttl_s
                        self._token_expires_at = time() + use_ttl
                        self._last_token_source = "memory"
                        self._persist_token_to_store()
                        renewed_value = self._token_value
                except Exception:
                    do_login = True

        if do_login:
            return self.login(force=True)
        return renewed_value

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

    def _request_json_with_current_token(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], RequestDiagnostics]:
        """REST call with Content-Type + accessToken from memory/store only (no login/renew)."""
        if not self.has_valid_token():
            raise RuntimeError("ume_no_valid_token")
        url = self._build_url(path)
        m = str(method or "GET").upper()
        t0 = time()
        try:
            with self._client() as client:
                resp = client.request(m, url, params=params, json=body, headers=self._headers(include_token=True))
            marker = str(resp.headers.get("marker") or "").strip()
            is_end_raw = str(resp.headers.get("is-end-of-reply") or "").strip().lower()
            is_end_of_reply: bool | None = None
            if is_end_raw in {"true", "false"}:
                is_end_of_reply = is_end_raw == "true"
            if not resp.is_success:
                diag = RequestDiagnostics(
                    method=m,
                    path=path,
                    status_code=int(resp.status_code),
                    latency_ms=int((time() - t0) * 1000),
                    retry_count=0,
                    error_code=f"http_{int(resp.status_code)}",
                    marker=marker,
                    is_end_of_reply=is_end_of_reply,
                )
                raise RuntimeError(f"ume_request_failed:{resp.status_code}:{resp.text[:240]}")
            data = _coerce_dict(resp.json())
            diag = RequestDiagnostics(
                method=m,
                path=path,
                status_code=int(resp.status_code),
                latency_ms=int((time() - t0) * 1000),
                retry_count=0,
                marker=marker,
                is_end_of_reply=is_end_of_reply,
            )
            return data, diag
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"ume_request_failed:{str(exc)[:240]}") from exc

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
            marker = str(resp.headers.get("marker") or "").strip()
            is_end_raw = str(resp.headers.get("is-end-of-reply") or "").strip().lower()
            is_end_of_reply: bool | None = None
            if is_end_raw in {"true", "false"}:
                is_end_of_reply = is_end_raw == "true"

            if not resp.is_success:
                diag = RequestDiagnostics(
                    method=m,
                    path=path,
                    status_code=int(resp.status_code),
                    latency_ms=int((time() - t0) * 1000),
                    retry_count=retry_count,
                    error_code=f"http_{int(resp.status_code)}",
                    marker=marker,
                    is_end_of_reply=is_end_of_reply,
                )
                raise RuntimeError(f"ume_request_failed:{resp.status_code}:{resp.text[:240]}")
            data = _coerce_dict(resp.json())
            diag = RequestDiagnostics(
                method=m,
                path=path,
                status_code=int(resp.status_code),
                latency_ms=int((time() - t0) * 1000),
                retry_count=retry_count,
                marker=marker,
                is_end_of_reply=is_end_of_reply,
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

    def get_network_elements(
        self,
        *,
        limit: int | None = None,
        marker: str | None = None,
    ) -> tuple[list[dict[str, Any]], RequestDiagnostics]:
        limit_max = int(getattr(settings, "ume_limit_max", 5000) or 5000)
        limit_max = max(1, limit_max)
        page_size = int(limit or settings.ume_page_size or 1000)
        page_size = max(1, min(page_size, limit_max))
        params: dict[str, Any] = {
            "limit": page_size,
        }
        marker_value = str(marker or "").strip()
        if marker_value:
            params["marker"] = marker_value
        data, diag = self.request_json("GET", self.ne_path, params=params)
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
        marker: str | None = None,
    ) -> tuple[list[dict[str, Any]], RequestDiagnostics]:
        limit_max = int(getattr(settings, "ume_limit_max", 5000) or 5000)
        limit_max = max(1, limit_max)
        page_size = int(limit or settings.ume_page_size or 1000)
        page_size = max(1, min(page_size, limit_max))
        params: dict[str, Any] = {
            "is-uncleared": "true" if is_uncleared else "false",
            "limit": page_size,
        }
        marker_value = str(marker or "").strip()
        if marker_value:
            params["marker"] = marker_value
        data, diag = self.request_json("GET", self.alarms_path, params=params)
        rows = self._extract_named_list(data, ["alarm-list", "alarm"])
        return rows, diag

    def _extract_subscription_output(self, payload: dict[str, Any]) -> tuple[str, str]:
        sub_id = ""
        uri = ""

        def walk(node: Any) -> None:
            nonlocal sub_id, uri
            if isinstance(node, dict):
                for k, v in node.items():
                    key = str(k).lower()
                    if not sub_id and key == "id" and isinstance(v, str):
                        sub_id = v.strip()
                    if not uri and key == "uri" and isinstance(v, str):
                        uri = v.strip()
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return sub_id, uri

    def establish_alarm_subscription(self, *, topic: str | None = None) -> tuple[str, str]:
        """POST establish-subscription using token from shared store (no login/renew in this path)."""
        self._sync_token_from_store()
        if not self.has_valid_token():
            raise RuntimeError("ume_establish_subscription_no_valid_token")
        topic_val = str(topic if topic is not None else self.notification_topic).strip() or "ALARM"
        body = {"input": {"topic": topic_val}}
        data, _diag = self._request_json_with_current_token("POST", self.notification_establish_path, body=body)
        sub_id, uri = self._extract_subscription_output(data)
        if not sub_id or not uri:
            raise RuntimeError("ume_establish_subscription_failed:missing_id_or_uri")
        return sub_id, uri

    def delete_alarm_subscription(self, subscription_id: str) -> None:
        sub_id = str(subscription_id or "").strip()
        if not sub_id:
            return
        self._sync_token_from_store()
        if not self.has_valid_token():
            return
        body = {"input": {"id": sub_id}}
        try:
            self._request_json_with_current_token("POST", self.notification_delete_path, body=body)
        except Exception:
            return

    def has_valid_token(self) -> bool:
        """True when a non-expired token is present in memory (call _sync_token_from_store first)."""
        token = self._token_value.strip()
        if not token:
            return False
        now = time()
        return now < (self._token_expires_at - self.token_refresh_skew_s)

    def ws_auth_headers(self) -> dict[str, str]:
        """
        Headers for WSS handshake — same fields as REST (_headers).
        Does not login/renew; relies on shared token store (token_keepalive / other sync paths).
        """
        self._sync_token_from_store()
        if not self.has_valid_token():
            raise RuntimeError("ume_ws_no_valid_token")
        return self._headers(include_token=True)
