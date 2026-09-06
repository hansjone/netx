---
name: netx-ops
description: netx 运维：告警、网元、只读 CLI。
---

# netx-ops（告警 · 清单 · 纳管登录）

处理 netx **告警 / 网元清单 / 只读 CLI** 时遵循本手册。  
画布布图不在本 skill（见 **netx-topology**）。本手册里的「路径」仅指 `findTopologyPaths` 查两端关联。

**工具名**：DSH 为 `netx__` + stem（如 `netx__queryNmsAlarms`）；MCP 多为同名裸 stem。下文一律写 stem。

**两套「一批」（勿混）：**

| 说法 | 含义 | 写法 |
|------|------|------|
| **多台一批** | 一次工具调用打多台设备 | `nms_ne_ids`/`ne_ids` + 共享 `commands`，或 `targets` |
| **同台多条** | 一次工具调用、**一台**上发多条命令 | 单个 `ne_id`/`nms_ne_id` + `commands:[…]` |

禁止：同轮对多台各调一次 `execManagedNe`（假并行、会串行）。

---

## 1. 总原则

1. **先新鲜度，再结论**：`runNmsDiagnostics` 或 `aggregateNmsAlarms` → 读 `meta.last_seen_*`。过旧则按快照：时间窗落在 min~max 内，禁止默认「现在−30 分钟」。
2. **证据优先**：有告警须有级别计数和/或 Top `host_name`；否则写「证据不足」，禁止臆造根因。
3. **对用户**：只展示 **host_name**；**严禁**出现任何 UUID/`ne_id`。无 `host_name` 的行当垃圾丢弃（不顶替 label、不提「缺失」）。工具入参仍可用 id，但不得写入对用户可见正文。
4. **短问**：能 ≤3 次工具调用就闭环的，先聚合/过滤，禁止无过滤翻全库。复杂关联（dying gasp、A<>B CLI）不受 3 次硬顶，但仍须有过滤与目标。
5. **「能否登录」**：必须走 §3；禁止只查 inventory 下结论。

---

## 2. 工具选用（按需，非逐步必跑）

按意图选步，**不是**每次从 ① 跑到 ⑧。

| 意图 | 工具 |
|------|------|
| 新鲜度 / 态势 / Top | `runNmsDiagnostics`、`aggregateNmsAlarms` |
| 一页现告警 | `queryNmsAlarms` |
| 可引用明细 | `queryNmsAlarmsRaw`（`field_preset=evidence`）；字段不明可先 `listNmsAlarmFields` |
| 自定义聚合 | `aggregateNmsAlarmsRaw`（如 `group_by=alarm_host_name`） |
| 复杂条件 | `sqlQueryNms`（只读 SELECT，设超时） |
| 绰号→真名 / 是否在 NMS | `queryNmsNeInventory` / `getNmsNe` |
| 两端路径 / 对端 | `findTopologyPaths` |
| 只读登录 | `listCliTargets` 或 `listManagedNe` → **一次** `execManagedNe` |

`getManagedNe` **仅**接受纳管 `ne_id`。NMS 侧 id 用 `execManagedNe(nms_ne_id=…)`，禁止塞进 `getManagedNe`。

---

## 3. 「能否登录」决策树（强制）

1. `listCliTargets(keyword=主机或IP)` **或** `listManagedNe(keyword=…, connect_status=pass)`  
   - **命中** → `execManagedNe`，命令按厂商：`show version`（中兴/Cisco 等）或 `display version`（华为）；以实测为准  
   - **未命中** → `queryNmsNeInventory(keyword=…)`：说明 NMS 有无及 `connection_status`；并写明：**未进 netx CLI 通道则本通道不能登录**（不等于设备不存在）
2. 已在 CLI 目标列表中：禁止只凭 inventory 说能/不能登。
3. 本会话 `listCliTargets`、`listManagedNe` **各最多一次**，缓存结果；同台多条命令进同一次 `commands[]`。

---

## 4. CLI：多台一批（强制）

查 **≥2 台** 时：必须 **一次** `execManagedNe`。

- 命令相同：`nms_ne_ids` 或 `ne_ids` + 共享 `commands`
- 命令不同（厂商/角色不同）：`targets=[{nms_ne_id|ne_id, commands:[…]}, …]`，仍一次调用
- 超时：加大 `read_timeout_sec` 或减条数；禁止同参盲重试
- 只读：`show` / `display` / `ping` / `traceroute` 及带 `?` 的探索；配置类不做

**✓ 同命令多台**

```json
{ "nms_ne_ids": ["uuid-a", "uuid-b"], "commands": ["show version"], "concurrency": 4 }
```

**✓ 混厂商（多台一批）**

```json
{
  "targets": [
    { "nms_ne_id": "uuid-zte", "commands": ["show opticalinfo brief"] },
    { "nms_ne_id": "uuid-hw", "commands": ["display optical-module brief"] }
  ],
  "read_timeout_sec": 90
}
```

（示例中的 uuid 仅作工具入参示意，**禁止**出现在对用户回复里。）

记不清命令、要用 `?` 探索 → 见 **§8**（同台多条分裂，不是多台同探）。

---

## 5. 现场短问配方

| 用户说法 | 做法 |
|----------|------|
| Critical Top / 高危排名 | `aggregateNmsAlarms(severity=critical, top_ne=20)` |
| 现网告警多少 / 态势 | `runNmsDiagnostics` 或 `aggregateNmsAlarms` → 级别 + 新鲜度 |
| 断纤 / LOS / 光缆 | Raw：`keyword=LOS` 和/或 `Fiber Break` → **有 host_name 的列表 + 计数** |
| 光功率**门限**（某区域） | Raw：`keyword=optical power`（或 Input optical power）+ 主机名前缀；**禁止**当断纤配方 |
| 离线 / BN EMS / 失联 | Raw：`keyword=BN EMS` 或 communication failure |
| dying gasp | 本端 dying gasp → `findTopologyPaths`/端口找对端 → 对端近时窗 BN EMS；**禁止只答一端** |
| CRC / 拥塞 bandwidth / license / 风扇温度 | Raw 对应 keyword；区域用主机名前缀 |
| 单机当前告警（已给 host） | 限定该 `host_name`；**禁止**跑无关日报/license 流程 |
| 告警码 NNNN | 按 code 过滤；只列有 `host_name` 的 |
| 时间窗（如 17:50–18:15） | 先新鲜度；时区以用户为准（未声明则沿用对话语境，现场常见 WIB/UTC+7） |
| 能否登录 / SSH | §3 |
| A<>B 容量 / 两端光功率 | **两端端口光模块 CLI 实读**，不是带宽利用率告警 tally（除非用户只要告警）：解析两端 host → 路径/LLDP → **多台一批** optic CLI |
| 哪段断了 + LOS 主机 | `object_name` + `findTopologyPaths` |
| BGP/OSPF/LDP 等 | 指定 host 或双端协议类 Raw；要比时间就对齐；**默认不当断纤** |

### 口语约定

- **区域** = `host_name` 在第一个 `-` 之前的前缀（如 `ACH-`），大小写不敏感 starts-with。
- **A<>B / capacity / 两端光功率** = 互联口 SFP/光功率 CLI；≠「带宽利用率超阈值」告警清单。
- **断纤清单** ≠ **光功率门限清单**（后者 keyword=`optical power`）。
- 绰号：先 `queryNmsNeInventory` 解析成 `host_name`；解析不到则说明无法解析，禁止瞎编。
- 「继续 / YES / 确认」：接着上一未完成任务，不整段重开。

### 常见 cause 子串（keyword / 证据标签）

| 意图 | 典型子串 |
|------|----------|
| 断纤 / LOS | `ETPI) LOS`、`Fiber Break`、`Missing laser module` |
| 光功率门限 | `Input optical power(dBm) threshold`、`Output optical power` |
| 拥塞 | `bandwidth usage rate` |
| CRC | `CRC error` |
| 离线 | `BN EMS`、`NE communication failure` |
| dying gasp | `Remote dying gasp` |
| License | `Permanent license`、`No enough license` |
| 环境 | `System Power off`、`undervoltage`、`temperature`、`Fan` |

Neighbour / PW / Tunnel 等控制面量多 **不当断纤**，除非用户问的就是该协议族。

---

## 6. 禁止

1. 无 severity / keyword / host / 区域 / 时间过滤翻全量告警。  
2. 把 NMS UUID 当作 `getManagedNe` 的 `ne_id`。  
3. 同轮对多台各调一次 `execManagedNe`。  
4. 光功率门限清单误用断纤/LOS 配方。  
5. 单机告警问句跑无关定时/license 流程。  
6. 终稿只有过程叙述、无 Result/Evidence。  
7. CLI 失败后对**同一错误命令、同一台**盲重试（应换命令或 `?` 探索）。  
8. 对用户输出 UUID，或用无 `host_name` 的行凑数。  
9. 因一台设备命令失败，就放弃该命令在其他设备上的使用。

---

## 7. 终稿

`*主题 — 范围*` + Result / Evidence / Next；短、可扫读；大表只给 Top（且仅含有 `host_name` 的）。  
速查：[reference.md](reference.md)。

---

## 8. 附录：厂商差异与 `?` 探索

仅在记不清命令、报错或需盲查时用。与 §4「多台一批」不同：此处是 **一台设备上多条探索命令**。

### 厂商前缀

| 厂商 | 只读习惯 |
|------|----------|
| 华为（含部分 VRP） | 多为 `display …` |
| 中兴 / Cisco / 多数其他 | 多为 `show …` |

混厂商多台：用 `targets`。同厂商多台：可先共享 `commands`，但须接受下面「同厂也可能不一致」。

### 同厂不同设备命令也可能不一致

检查整网时**一定会**碰到：同为中兴/华为，A 台能敲的命令 B 台报错或参数不同（版本、牌号、角色差异）。

- **禁止**因一台失败就认定「这条命令全网作废、以后都不用」。
- **应**：失败台单独换命令或走 `?` 探索；其余已成功的台继续用原命令。
- **优先复用本会话已成功过的命令**（含同厂其他台验证过的）：新台/下一批先试这些，再对失败子集另探；不要一失败就换全员命令。
- 多台一批时：用 `targets` 给失败台换命令，或先一批共同命令 → 只对失败子集再一批探测；不要为了一台把成功台的结果丢掉重来。

### `?` 规则（`show` / `display` / `ping` 等同一套）

1. 报错中的 **`^`** 标出错位置 → **`^` 之前**已正确 → 对该前缀加 `?` 列下一级。  
2. `?` 可紧接在部分单词后（`show optical?`、`display inter?`），不必强制空格再写 `?`。  
3. 输出含 `<cr>` → 命令已完整，可回车执行。  
4. Incomplete → 未写完，继续 `?`；Unrecognized → 走错，退回上一层换词。  
5. **禁止**对同一错误整句反复重试。

例：

```text
<r1>display interface briaf
                      ^
Error: Wrong parameter found at '^' position.
```

→ 改为 `display interface ?`，再选 `brief` 等合法下级。`show` 同理。

### 同台分裂（一台、多条、一次调用）

某一层 `… ?` 列出多个候选且还需下钻时：在 **同一台** 一次 `execManagedNe` 的 `commands[]` 中放入多条同级探索，综合结果再往下裂。  
**不是**多台同时探同一条 `?`。

例（已见 `display ip routing-table ?` 后）：

```text
display ip routing-table protocol ?
display ip routing-table all-vpn-instance ?
display ip routing-table all-routes ?
```

对应一次调用形如：`execManagedNe(nms_ne_id=…, commands=["display ip routing-table protocol ?", "… all-vpn-instance ?", "… all-routes ?"])`。

### ZTE 光模块

优先 `show opticalinfo brief` → 其次 `show optical brief` → 仍不对则 `show optical?` / `show opticalinfo?`；同级候选用上面的同台分裂，勿盲猜整句。
