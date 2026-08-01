# NetX Web 前端规范

## 技术栈

- React 19 + TypeScript + Vite
- React Router（工作台 + 模块页）
- TanStack Query（服务端状态）
- 自研 i18n（`src/i18n/`，无额外依赖）

## 目录结构

```
src/
  config/modules.ts      # 模块注册表（工作台卡片、路由、标题 i18n）
  constants/queryKeys.ts # React Query key
  components/            # 可复用 UI（无业务 API 调用）
  hooks/                 # 通用 hooks（Toast、窗口注册）
  i18n/                  # 文案 zh / en
  layout/                # AppLayout、ErrorBoundary
  pages/                 # 页面（按路由）
  services/api.ts        # HTTP 封装与 API 函数
  types.ts               # 与后端契约类型
  utils/
    tabChannel.ts        # 跨标签页聚焦（BroadcastChannel）
    workbench.ts         # 返回工作台
    moduleWindows.ts     # 打开/聚焦模块页签
    display.ts           # 纯展示辅助
```

## 路由与模块

| 路径 | 页面 | moduleId |
|------|------|----------|
| `/` | 工作台 | — |
| `/ume` | UME 同步 | `ume` |
| `/ne` | 网元管理（CRUD / 连通性） | `ne` |
| `/network` | 网络管理（左栏 + 子路由） | `network` |
| `/network/devices` | 设备列表（只读：托管 + UME） | `network` |
| `/network/alarms` | 告警信息 | `network` |
| `/network/configs` | 配置信息（同步快照） | `network` |
| `/network/webcrt` | redirect → `/webcrt`（保留 query） | — |
| `/network/topology` | redirect → `/topology` | — |
| `/network/tasks/collect` | 采集任务 | `network` |
| `/network/tasks/config-sync` | 配置同步 | `network` |
| `/network/tasks/port-traffic` | 端口流量监控（设备管理） | `network` |
| `/network/tasks/port-traffic/wall` | 流量大屏列表（打开独立页签） | `network` |
| `/port-traffic/wall/:boardId` | 流量大屏专有页签（无网络侧栏） | `port-traffic-wall` |
| `/topology` | 拓扑管理（模式化编辑器：选择/平移/拖动/连线、框选、自动布局、拖放添加） | `topology` |
| `/webcrt` | WebCRT 终端 | `webcrt` |
| `/collect` | redirect → `/network/tasks/collect` | — |

菜单树见 `config/networkNav.ts`。**新增网络子页：**

1. 在 `networkNav.ts` 增加叶子项
2. 在 `App.tsx` `/network` 下增加子 `<Route>`
3. 补充 i18n `network.*`

**新增工作台模块仍改 `config/modules.ts`：**

1. 在 `MODULES` 增加一项（`moduleId`、`path`、`section`、i18n key、`iconTone`）
2. 在 `App.tsx` 增加 `<Route path="..." element={...} />`
3. 在 `i18n/zh.ts` 与 `en.ts` 补充文案
4. 工作台卡片由 `modulesInSection` 自动生成，点击走 `openOrFocusModule`

## 跨标签页导航

- **工作台 → 模块**：`openOrFocusModule`（同 `moduleId` 仅一个标签，已打开则聚焦）
- **模块 → 工作台**：顶栏四格图标，`returnToWorkbench`（聚焦原工作台标签；已关闭则新开）
- **流量大屏**：列表在网络管理内；打开单板用 `openNewModuleWindow`（`/port-traffic/wall/:boardId`），与 `network` 单例页签隔离
- 实现：`utils/tabChannel.ts` + 命名窗口 `netx-module-{id}` / `netx-workbench`

## i18n

- 所有面向用户的文案走 `useI18n().t("dotted.key")`
- 表头/字段名等技术列可保持英文
- 语言存在 `localStorage`（`netx-locale`），顶栏 ⋯ 切换

## 数据请求

- API 仅写在 `services/api.ts`
- Query key 集中在 `constants/queryKeys.ts`
- 失效缓存用 prefix key（如 `queryKeys.umeSyncStatusAll`）
- 顶栏连接状态：`App` 轮询 `GET /v1/integrations/status`（5s），展示 **netx api** 与 **oclaw bridge**（含延迟 / 错误类型）

## 网元管理（独立于 UME）

- API：`/v1/managed-ne/*`（CRUD、导入、连通性测试）
- 环境变量：`NETX_CREDENTIAL_SECRET_KEY`（Fernet，用于加密存储 SSH 密码）
- 导入列：`device_type,ip,username,password,port,protocol,name,vendor`
- 连通性测试成功后会**始终**用探测到的设备名覆盖「名称」

## 批量采集

- API：`/v1/ne-collections/*`（仅 `connect_status=pass` 的网元可参与）
- 采集日志目录：`NETX_NE_COLLECTION_DATA_DIR`（默认 `data/ne_collections`）
- 命令每行一条，`#` 为注释；输出格式与旧版 NetX 采集 `.txt` 一致

## 配置同步

- API：`/v1/config-sync/*`（策略、看板、周期、快照）
- 存储：PostgreSQL `ne_config_snapshot` / `ne_config_history`（zlib BYTEA）
- 范围：ManagedNE + UME CLI 目标；厂商固定只读命令矩阵
- 调度：`NETX_CONFIG_SYNC_SCHEDULER_ENABLED`（默认开），周期天数策略可配（默认 3 天）
- 默认策略：`enabled=false`（首次无自动任务，需在页面手动开启周期调度或点「立即同步」）
- 单飞：同一时刻只允许一个 `running|pending|paused` 周期；上轮未结束时不会开启新周期
- 周期状态：全部任务跑完即为 `success`；单网元失败只计入 `fail_count`，不把整轮标为失败
- 崩溃续跑：启动时把中断的 `running` 任务重新入队并继续，占用单飞槽位，避免与新周期重叠
- 进程启动宽限：`NETX_CONFIG_SYNC_STARTUP_GRACE_SEC`（默认 3600）仅约束**新建**自动周期，不影响续跑
- 前端：`/network/tasks/config-sync`（看板）+ `/network/configs`（查看）

## 端口流量监控

- API：`/v1/port-traffic/devices/*`（设备 CRUD、interfaces、samples、compare、dashboard）；`discover/ports` 仍按网元拉取接口
- **设备中心**：每台网元（`source`+`ne_id`）一份监控配置；周期/保留/启停挂在设备上；接口归属于该设备
- 唯一性：同一物理口 `(source, ne_id, ifname)` 全局仅允许一个 **active** 监控行，避免重复 CLI 采集
- 调度：按设备到期；一轮采集对该设备 **登录一次** 后批量 `show interface`
- 手工映射：大屏可选其它设备接口作基线叠图；可与周期偏移叠加
- 厂商：ZTE / 华为 / 思科；解析速率 **bit/s**；缺厂商 util 时按 bps/bw 回算
- 调度开关：`NETX_PORT_TRAFFIC_SCHEDULER_ENABLED`（默认开），tick `NETX_PORT_TRAFFIC_SCHEDULER_TICK_SEC`（默认 15）
- 保留：按设备 `retention_days` 清理过期 sample（周对比建议 ≥8 天）
- 前端：`/network/tasks/port-traffic`（设备列表 / 向导 / 编辑 / 采集日志）；`/network/tasks/port-traffic/wall`（大屏列表）；`/port-traffic/wall/:boardId`（独立大屏页签，`openNewModuleWindow`，与网络管理页签互不影响）
- 定制大屏：`/v1/port-traffic/boards*`（Board + Panel）；整板 PUT panels 保存；刷新/换页不丢；图数据仍走 compare
- 采集日志：`GET /v1/port-traffic/devices/{id}/events`；失败写入 `port_traffic_event`，列表操作可查看
- 支持拓扑深链：`?ne_id=&source=managed|ume&ifname=` 打开向导并预填网元

## 拓扑管理（Fabric + View）

- 事实库：`topo_fabric_node` / `topo_fabric_edge`（按 5 万网元 / 100 万链路设计；物理层仅 LLDP）
- 视图：`topo_view` + `topo_view_node`（坐标）；旧 `topology_map/node/edge` 已退役
- API：`/v1/topology/fabric/*`（summary / nodes / edges / neighborhood / discover job）、`/v1/topology/views*`
- 前端：`@xyflow/react`；侧栏上图、拖拽保存坐标（PATCH positions）、布局（dagre/力导向/网格/环形）、轮询 LLDP job
- MCP：`getTopologySummary`、`queryTopologyNodes/Edges`、`getTopologyNeighborhood`、`runLldpDiscover`、`getLldpDiscoverJob`、`listTopologyViews`、`getTopologyView`
- BGP / 隧道 / L2VPN：`layer` 预留，实现 TODO

## WebCRT

- API：`POST /v1/webcrt/sessions`（`ne_id` 或 `ume_ne_id`；可选 `encoding`/`post_login_commands`/`async_connect`）、`WS /v1/webcrt/sessions/{id}/ws`、`DELETE /v1/webcrt/sessions/{id}`
- SFTP（直连 SSH）：`POST /v1/webcrt/sftp/list|download|upload`
- 目标列表复用 `/v1/cli/targets`（托管 + UME，搜索分页；`source=all|managed|ume`）
- 凭据：托管走网元自身账号；UME 走 CLI 连接模板（`resolve_cli_target`）
- 前端：CRT 风格左右分栏（收藏/最近 + 多标签终端）；粘贴节流、选区复制、Break、Ctrl+F、Button Bar、广播、编码/字号
- WS：stdout 二进制帧合批；stdin 短合批；建连可异步（POST 立即返回，进度经 WS `connecting`→`connected`）
- 审计 / 会话日志：`NETX_WEBCRT_DATA_DIR`（`audit.jsonl` + `sessions/*.log`）
- 限流 / 保活：`NETX_WEBCRT_MAX_SESSIONS`、`NETX_WEBCRT_IDLE_TIMEOUT_SEC`、`NETX_WEBCRT_KEEPALIVE_SEC`、`NETX_WEBCRT_ANTI_IDLE_SEC`

## Toast

- 使用 `ToastProvider`（`main.tsx`）+ `useToast()`
- 禁止页面内单独维护一套 toast 状态

## 样式

- 全局样式：`index.css`（工作台浅色主题）
- 不使用 CSS Modules；类名 BEM 风格：`app-brand__logo`、`wb-card`

## 构建

```bash
cd web && npm run build
```

开发：`npm run dev`（需后端 API 或 Vite proxy 配置）
