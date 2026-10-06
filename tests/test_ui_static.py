"""Tests for optional SPA static hosting."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_api_only_root_when_no_dist(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("NETX_UI_DIST_DIR", str(tmp_path / "missing"))
    # Re-import settings + app with patched env is heavy; call helpers directly.
    from netx_api.ui_static import resolve_ui_dist
    from netx_api import config as cfg

    monkeypatch.setattr(cfg.settings, "ui_dist_dir", str(tmp_path / "missing"))
    assert resolve_ui_dist() is None


def test_resolve_ui_dist_when_index_present(monkeypatch, tmp_path: Path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>netx</title>", encoding="utf-8")
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")

    from netx_api import config as cfg
    from netx_api.ui_static import resolve_ui_dist

    monkeypatch.setattr(cfg.settings, "ui_dist_dir", str(dist))
    assert resolve_ui_dist() == dist.resolve()


def test_mount_ui_serves_index(monkeypatch, tmp_path: Path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body>ui</body></html>", encoding="utf-8")
    (assets / "x.js").write_text("ok", encoding="utf-8")

    from fastapi import FastAPI

    from netx_api import config as cfg
    from netx_api.ui_static import mount_ui_if_present

    monkeypatch.setattr(cfg.settings, "ui_dist_dir", str(dist))
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    assert mount_ui_if_present(app) is True
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "ui" in r.text
    r2 = client.get("/assets/x.js")
    assert r2.status_code == 200
    assert r2.text == "ok"
    r3 = client.get("/login")
    assert r3.status_code == 200
    assert "ui" in r3.text
    assert client.get("/health").json()["status"] == "ok"


def test_spa_paths_are_public():
    from netx_api.auth_middleware import _is_public

    assert _is_public("/login") is True
    assert _is_public("/topology") is True
    assert _is_public("/assets/app.js") is True
    assert _is_public("/v1/managed-ne") is False
    assert _is_public("/health") is True
    assert _is_public("/v1/auth/login") is True
