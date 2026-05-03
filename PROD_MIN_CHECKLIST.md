# netx Production Minimum Checklist

## Security
- Replace local shared token with a strong secret and rotate it regularly.
- Keep `.env` and `oclaw/_local/system.env` out of Git (already ignored).
- Restrict access to `127.0.0.1` or internal network only.

## Runtime
- Ensure PostgreSQL backup policy exists (daily logical backup + retention).
- Run `oclaw` and `netx` under process managers (systemd/Windows service/pm2 equivalent).
- Enable auto-restart and startup-at-boot for both services.

## Observability
- Health checks:
  - `oclaw`: `/admin/api/ops-ai/health` (with Bearer token)
  - `netx`: `/v1/integrations/status`
- Alert when `oclaw_bridge.status != up` for more than 2 polling cycles.
- Alert when `db.status != up` or `latency_ms` exceeds threshold.

## Operations
- Keep one documented restart order:
  1) PostgreSQL
  2) `oclaw`
  3) `netx`
- Validate after restart:
  - `GET /v1/integrations/status` returns all major components as `up`.
- Keep parser config and importer changes versioned and reviewed before release.

