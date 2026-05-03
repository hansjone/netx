# netx

Independent operations tool (web + MCP) for alarm-centric workflows.

## Phase 1 scope

- Import ZTE Alarm Monitor Excel
- Normalize and store alarms into an isolated PostgreSQL
- Query and aggregate alarms via REST API
- Expose the same read capabilities through MCP stdio tools
- Quick diagnostics endpoint (`/v1/diagnostics`)
- AP analysis bridge to oclaw (`/v1/ap/analyze`)

## Run locally

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Set environment variables:

```powershell
$env:NETX_DATABASE_URL = "postgresql+psycopg://netx:netx@127.0.0.1:5432/netx"
$env:NETX_HOST = "127.0.0.1"
$env:NETX_PORT = "8890"
```

Or create a local `.env` (recommended for persistent local run):

```env
NETX_DATABASE_URL=postgresql+psycopg://postgres:admin123@127.0.0.1:5432/netx
NETX_OCLAW_ANALYZE_TOKEN=admin123
NETX_OCLAW_HEALTH_URL=http://127.0.0.1:8787/admin/api/ops-ai/health
```

3. Start API:

```powershell
python -m netx_api.main
```

Or use automation scripts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_netx.ps1 -SkipInstall -Background
```

Start API + Vite together:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_netx.ps1 -SkipInstall -Background -WithWeb
```

Stop:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_netx.ps1 -Force
```

Primary web UI (Vite): `http://127.0.0.1:5173/`  
API base: `http://127.0.0.1:8890/`

4. Optional: start MCP server (for oclaw integration):

```powershell
python -m netx_api.mcp_server
```

Or use MCP install payload:

- `netx/mcp_install_payload.json`

## Industrial UI (Vite + React + TS)

Frontend project lives in:

- `netx/web`

Run:

```powershell
cd web
npm install
npm run dev
```

Dev server: `http://127.0.0.1:5173`  
`vite.config.ts` proxies `/v1/*` to `http://127.0.0.1:8890`.

`8890` is API-only and no longer serves the main UI page.

## Useful API endpoints

- `POST /v1/alarms/import`
- `GET /v1/batches`
- `GET /v1/batches/{batch_id}`
- `GET /v1/batches/{batch_id}/errors.csv`
- `GET /v1/alarms`
- `GET /v1/alarms/aggregate`
- `GET /v1/diagnostics?batch_id=...`
- `GET /v1/integrations/status`
- `POST /v1/ap/analyze`

Web UI now includes an **AI analyze panel** that calls `/v1/ap/analyze` directly.

## AP bridge auth (optional but recommended)

Set the same token on both sides:

- `oclaw` env: `OCLAW_OPS_AI_SHARED_TOKEN=<token>`
- `netx` env: `NETX_OCLAW_ANALYZE_TOKEN=<token>`

Optional: configure oclaw health check endpoint (defaults shown in `.env.example`):

- `NETX_OCLAW_HEALTH_URL=http://127.0.0.1:8787/admin/api/ops-ai/health`

`analyze-sync` runs a full LLM + gateway turn in oclaw; if netx reports timeout errors, raise the read timeout (seconds):

- `NETX_OCLAW_ANALYZE_READ_TIMEOUT_SEC=180` (default `180`; was effectively ~35s before)

## Key sample file

Phase 1 parser is tuned for:

`D:/project/chatgpt/fm-active-Alarm Monitor-ZTE-CHEN-20260423100105/fm-active-Alarm Monitor-ZTE-CHEN-20260423100105.xlsx`
