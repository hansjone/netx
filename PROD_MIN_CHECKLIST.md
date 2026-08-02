# netx Production Minimum Checklist

## Security
- Prefer empty `NETX_AUTH_SECRET` so each install auto-writes `data/auth/jwt_secret` (do not commit that file). Set an explicit secret only for multi-node shared signing.
- Leave `NETX_DOCS_ENABLED` unset/false so `/docs` and OpenAPI stay off (set `true` only in lab).
- Keep `NETX_UME_VERIFY_TLS=true` (or pin a CA); avoid `false` on non-lab hosts.
- Binding `NETX_HOST` to a non-loopback address with lab defaults is refused unless `NETX_ALLOW_INSECURE_DEFAULTS=1`.
- Prefer scoped API tokens (MCP default excludes `webcrt:session` / `sql:query`).
- Keep `.env` and `oclaw/_local/system.env` out of Git (already ignored).
- Restrict access to `127.0.0.1` or internal network only.

## Runtime
- Ensure PostgreSQL backup policy exists (daily logical backup + retention).
- Prefer Alembic: see [docs/ALEMBIC.md](docs/ALEMBIC.md). After `alembic upgrade head`, set `NETX_SKIP_LEGACY_STARTUP_DDL=true`.
- Optional: `NETX_RUN_INLINE_SCHEDULERS=false` and run `python -m netx_api.worker` for collectors.
- Run `oclaw` and `netx` under process managers (systemd/Windows service/pm2 equivalent).
- Enable auto-restart and startup-at-boot for both services.

## Observability
- Health checks:
  - `oclaw`: `/admin/api/ops-ai/health` (with Bearer token)
  - `netx` liveness: `/health/live`
  - `netx` readiness: `/health/ready`
  - `netx` integrations: `/v1/integrations/status`
- Alert when `oclaw_bridge.status != up` for more than 2 polling cycles.
- Alert when `db.status != up` or `latency_ms` exceeds threshold.

## Operations
- Keep one documented restart order:
  1) PostgreSQL
  2) `oclaw`
  3) `netx` (and worker if split)
- Validate after restart:
  - `GET /v1/integrations/status` returns all major components as `up`.
- Keep parser config and importer changes versioned and reviewed before release.
