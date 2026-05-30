# netx

Independent operations tool (web + MCP) for alarm-centric workflows.

Licensed under the [MIT License](LICENSE). Security reports: [SECURITY.md](SECURITY.md).

## Phase 1 scope

- Import ZTE Alarm Monitor Excel
- Normalize and store alarms into an isolated PostgreSQL
- Query and aggregate alarms via REST API
- Expose the same read capabilities through MCP stdio tools
- Quick diagnostics endpoint (`/v1/diagnostics`)
- AP analysis bridge to oclaw (`/v1/ap/analyze`)

## Environment setup (from scratch)

### 0) Prerequisites

- Python `3.11+` (project currently tested on newer versions too)
- Node.js `20+` (includes npm) for frontend
- PostgreSQL `14+`
- PowerShell (Windows startup scripts use `.ps1`)

Quick check:

```powershell
python --version
node --version
npm --version
psql --version
```

If Node/npm is missing (Windows example):

```powershell
winget install OpenJS.NodeJS.LTS
```

### 1) Initialize PostgreSQL role/database (Windows, recommended)

For first-time setup on a new machine, run:

```powershell
cd netx
powershell -ExecutionPolicy Bypass -File .\scripts\init_pg.ps1
```

The script is idempotent: it creates/repairs role `netx`, database `netx`, grants privileges, and verifies connection.

Common options:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_pg.ps1 `
  -SuperUser postgres `
  -SuperPassword "your-postgres-password" `
  -NetxUser netx `
  -NetxPassword "your-netx-password" `
  -NetxDatabase netx
```

### 2) Python virtual environment and backend deps

```powershell
cd netx
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

### 3) Frontend deps (npm)

```powershell
cd .\web
npm install
cd ..
```

### 4) Configure environment variables

Option A: temporary env vars in current shell

```powershell
$env:NETX_DATABASE_URL = "postgresql+psycopg://netx:netx@127.0.0.1:5432/netx"
$env:NETX_HOST = "127.0.0.1"
$env:NETX_PORT = "8890"
```

Option B: local `.env` (recommended)

```env
NETX_DATABASE_URL=postgresql+psycopg://postgres:admin123@127.0.0.1:5432/netx
NETX_OCLAW_ANALYZE_TOKEN=admin123
NETX_OCLAW_HEALTH_URL=http://127.0.0.1:8787/admin/api/ops-ai/health
```

### 5) Start services

Direct backend start:

```powershell
.\.venv\Scripts\python -m netx_api.main
```

Or use automation scripts (recommended):

- API only (background):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_netx.ps1 -SkipInstall -Background
```

- API + Vite UI (background):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_netx.ps1 -SkipInstall -Background -WithWeb
```

Stop all started services:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_netx.ps1 -Force
```

Primary web UI (Vite): `http://127.0.0.1:5173/`  
API base: `http://127.0.0.1:8890/`

### 6) MCP（Cursor / oclaw / Claude）

先启动 netx API（§5），再在 **MCP 宿主同机** 安装轻量客户端并配置。

**完整说明（安装、配置、更新、排错）见：[docs/MCP.md](docs/MCP.md)**

速查：

```powershell
pip install -e ./packages/netx-mcp
# 配置见 mcp.json，运行：
python -m netx_mcp
```

- 客户端配置：[`mcp.json`](mcp.json)（Cursor / oclaw Admin 粘贴同一份）
- oclaw 可选 payload：[`mcp_install_payload.json`](mcp_install_payload.json)
- 子包说明：[`packages/netx-mcp/README.md`](packages/netx-mcp/README.md)

## Useful API endpoints

- `POST /v1/alarms/import` (legacy import path, kept for compatibility)
- `GET /v1/batches`
- `GET /v1/batches/{batch_id}`
- `GET /v1/batches/{batch_id}/errors.csv`
- `GET /v1/alarms`
- `GET /v1/alarms/aggregate`
- `GET /v1/diagnostics?batch_id=...`
- `GET /v1/ume/alarms`
- `GET /v1/ume/alarms/aggregate`
- `GET /v1/ume/diagnostics`
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

Phase 1 parser (`netx_api/config/parsers/zte_alarm_monitor_v1.yaml`) remains available for historical compatibility. The recommended data source is UME sync (`ume_alarms_current`), while legacy import (`POST /v1/alarms/import`) is still kept available as a fallback path.

## Contributing

Fork / PR 流程与注意事项见仓库根目录 `CONTRIBUTING.md`。
