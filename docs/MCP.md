# netx MCP — 安装与更新

netx 通过 **stdio MCP** 把告警/网元能力暴露给 Cursor、Claude Desktop、oclaw 等宿主。MCP 进程只做 HTTP 客户端，**不直连数据库**；数据来自已运行的 netx REST API。

```
MCP 宿主 (Cursor / oclaw)  →  stdio  netx_mcp  →  HTTP  NETX_API_URL  →  netx API
```

| 组件 | 部署位置 | 说明 |
|------|----------|------|
| **netx API** | 本机或远端服务器 | `python -m netx_api.main`，默认 `http://127.0.0.1:8890` |
| **netx-mcp** | 与 MCP 宿主同机 | `pip install` 轻量包，仅需 Python + httpx |

---

## 前置条件

1. **netx API 已启动**且可访问：

   ```powershell
   curl http://127.0.0.1:8890/health
   ```

2. **Python 3.11+**（与 MCP 宿主使用的 `python` 一致）。

3. 远端 API 时：记下 base URL（无尾部 `/`），例如 `http://10.0.0.5:8890`。

---

## 1. 安装 `netx-mcp` 包

在 **运行 MCP 的那台机器**上执行（不必安装整套 netx 服务依赖）：

```powershell
cd D:\project\chatgpt\netx
pip install -e ./packages/netx-mcp
```

验证（**必须用 oclaw 启动时会调用的同一个 `python`**，见下方说明）：

```powershell
where python
python -c "import netx_mcp; print('ok')"
python -m netx_mcp
```

若你在 A 终端里 `import netx_mcp` 成功，但 oclaw Admin 安装/Health 无反应或失败，多半是 **pip 装到了另一个 Python**。请用 oclaw 实际用的解释器安装：

```powershell
# 把路径换成 where python 的第一条，或 oclaw 服务 venv 里的 python.exe
C:\Path\To\Same\python.exe -m pip install "git+https://github.com/hansjone/netx.git#subdirectory=packages/netx-mcp"
C:\Path\To\Same\python.exe -c "import netx_mcp; print('ok')"
```

从 GitHub 安装（无本地仓库时，**子目录必须是 `packages/netx-mcp`**）：

```powershell
pip install "git+https://github.com/hansjone/netx.git#subdirectory=packages/netx-mcp"
```

开发者在 netx 仓库根目录也可 `pip install -e .`（含 API）；MCP 仍推荐只装 `packages/netx-mcp`。

---

## 2. 环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `NETX_API_URL` | 否 | `http://127.0.0.1:8890` | netx REST 根地址，可指向远端 |
| `NETX_API_TOKEN` | 否 | 空 | Bearer；空则自动读 `data/auth/mcp_token`（API 首次启动生成） |
| `NETX_MCP_TOKEN_FILE` | 否 | `data/auth/mcp_token` | 默认 token 文件路径 |
| `NETX_LANG` | 否 | `zh` | `zh` / `en`，影响 API 文案 |
| `NETX_NE_EXEC_MAX_COMMANDS` | 否 | `5` | `execManagedNe` 单次最多命令数（硬上限 50）；API 与 MCP 需同设 |

本机默认端口时 **可不设任何变量**。启用登录后，先启动一次 netx API，会生成 `data/auth/mcp_token`；MCP 会自动带上该 token。若要把 token 写进 Cursor 配置：

```json
"NETX_API_TOKEN": "nxt_从文件复制的内容"
```

---

## 3. 在 Cursor / Claude Desktop 中配置

复制仓库中的 [`mcp.json`](../mcp.json)（或 [`packages/netx-mcp/mcp.json`](../packages/netx-mcp/mcp.json)）到客户端 MCP 配置，例如 Cursor：`.cursor/mcp.json`。

```json
{
  "mcpServers": {
    "netx": {
      "command": "python",
      "args": ["-m", "netx_mcp"],
      "env": {
        "NETX_API_URL": "http://127.0.0.1:8890",
        "NETX_LANG": "zh",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

（可选）显式设置 `"NETX_API_TOKEN": "nxt_..."`；不设则读仓库/`cwd` 下的 `data/auth/mcp_token`。

保存后 **重启 Cursor/客户端**，使 MCP 子进程重新拉起。

---

## 4. 在 oclaw Admin 中安装

与 Cursor **同一份** `mcpServers` JSON 即可，无需转成别的格式。

1. 完成上文 **§1**（用 **oclaw 同机同一个 `python`** 安装 `netx-mcp`）。
2. Admin → MCP → 粘贴 `mcp.json` 全文 → 点击 **Install from JSON**（安装状态在下方一行小字）。
3. **Health** → **Sync Tools**（应看到 **12** 个工具）。
4. 在 **MCP 专家绑定** 中为 ops 专家勾选 `server_id=netx`。

更细的 oclaw 说明（双轨内置工具、锚点注入等）见 oclaw 仓库：  
`oclaw/docs/NETX_MCP_INTEGRATION.md`。

可选：oclaw 专用字段展开版 [`mcp_install_payload.json`](../mcp_install_payload.json)（与 `mcp.json` 等价）。

---

## 5. 暴露的 12 个工具

| 类别 | 工具名 |
|------|--------|
| UME 告警 | `queryUmeAlarms`, `aggregateUmeAlarms`, `runUmeDiagnostics` |
| UME 网元 | `queryUmeNeInventory`, `getUmeNe` |
| UME 原始/SQL | `queryUmeAlarmsRaw`, `aggregateUmeAlarmsRaw`, `listUmeAlarmFields`, `sqlQueryUme` |
| 托管网元 CLI | `listManagedNe`, `getManagedNe`, `execManagedNe` |

oclaw 中名称带前缀：`mcp__netx__<toolName>`。

---

## 6. 更新 MCP

MCP 代码在 **`packages/netx-mcp`**（版本见 `packages/netx-mcp/pyproject.toml`）。更新步骤：

### 6.1 重装 `netx-mcp` 包

**远程 MCP 机 / 无本地 netx 仓库**（不必 `git pull`，每次更新执行一条即可）：

```powershell
pip install --upgrade "git+https://github.com/hansjone/netx.git#subdirectory=packages/netx-mcp"
```

**本机有 netx 仓库（开发）**：

```powershell
cd D:\project\chatgpt\netx
git pull
pip install -e ./packages/netx-mcp
```

确认版本（可选）：

```powershell
pip show netx-mcp
```

### 6.2 重启 MCP 宿主

| 宿主 | 操作 |
|------|------|
| **Cursor / Claude** | 完全退出客户端后重开，或重载 MCP |
| **oclaw** | 重启 gateway/主进程；Admin 对 `netx` 再点 **Health** → **Sync Tools** |

仅改 `NETX_API_URL` 等 env 时，同样需重启 MCP 子进程（或 oclaw 整进程）。

### 6.3 无需改配置的情况

- 只改 **netx API** 业务逻辑、未改 MCP 工具名/参数：重装 `netx-mcp` 可选；API 部署后 MCP 自动走新 API。
- 改了 **工具列表或 JSON schema**：必须重装 `netx-mcp` 并在宿主 **Sync Tools**。

### 6.4 从旧入口迁移

| 旧方式 | 新方式 |
|--------|--------|
| `python -m netx_api.mcp` | `python -m netx_mcp`（推荐） |
| `NETX_MCP_MODE=db` 直连库 | 已废弃；使用 HTTP 模式 |

根目录 `python -m netx_api.mcp` 仍会委托到 `netx_mcp`，新环境请只装 `netx-mcp` 包。

---

## 7. 自检

**API：**

```powershell
curl http://127.0.0.1:8890/health
```

**MCP 工具列表（需已 `pip install -e packages/netx-mcp`）：**

```powershell
cd D:\project\chatgpt\netx
python -m pytest packages/netx-mcp/tests/test_mcp_http.py -q
```

**手动 stdio（PowerShell 示例）：**

```powershell
$env:NETX_API_URL = "http://127.0.0.1:8890"
$p = Start-Process python -ArgumentList "-m","netx_mcp" -RedirectStandardInput pipe -RedirectStandardOutput pipe -NoNewWindow -PassThru
# 向 stdin 写入一行 JSON-RPC initialize / tools/list（见 packages/netx-mcp/tests）
```

---

## 8. 常见问题

| 现象 | 处理 |
|------|------|
| `ModuleNotFoundError: netx_mcp` | 用 **oclaw 同机同一个 `python.exe`** 执行 pip 安装（见 §1 验证） |
| **oclaw Admin 点安装「没反应」** | 1) 必须点 **Install from JSON**（只粘贴不点按钮无效）<br>2) 看安装区下方 **install status** 一行字（应出现 `[json] installing...` 或错误）<br>3) 浏览器 F12 → Console 是否有红色报错<br>4) 确认 JSON 为完整 `mcpServers` 或 `mcp_install_payload.json` 格式 |
| oclaw preflight `mcp_python_module_missing` | `python` 在 PATH 里但无 `netx_mcp`；对该 python 执行 git+pip 安装 |
| 工具调用连不上 API | 检查 `NETX_API_URL`、防火墙、远端 API 是否启动 |
| Windows 乱码 / JSON 解析失败 | 配置里保留 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`（见 `mcp.json`） |
| oclaw 工具数为 0 | Admin **Health → Sync Tools**；确认 `server_id=netx` 已绑定 ops 专家 |
| 仍想用仓库脚本路径 | 未 pip 安装时可临时 `"args": ["D:/.../netx_api/mcp_server.py"]`（开发用） |

---

## 相关文件

| 文件 | 用途 |
|------|------|
| [`packages/netx-mcp/`](../packages/netx-mcp/) | MCP 实现与 `pyproject.toml` |
| [`mcp.json`](../mcp.json) | Cursor / oclaw 粘贴用配置 |
| [`mcp_install_payload.json`](../mcp_install_payload.json) | oclaw 字段展开版（可选） |
| [`packages/netx-mcp/README.md`](../packages/netx-mcp/README.md) | 子包速查 |
