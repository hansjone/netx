# 割接对比：规范值契约

适用范围：业务对比（`BizCompare*`）和割接监控（`BizMigration*`）。两种模式用同一条身份流水线（`netx_api/biz_state/identity_pipeline.py`），跨厂商对比靠它把两侧设备的原始输出对齐到同一套“规范值”。

## 1. 流水线

同一行在某一侧（`before` / `after`）依次经过：

1. **归一化**：接口字段去内部空格 → 厂商内规范名（`VENDOR_CANONICAL`，如华为 `GigabitEthernet`→`GE`）→ 模板别名规则（`iface_normalize_rules`，如 `GE`→`gei`）；键字段按 `field_rules[].normalize`（`lower`/`upper`/`mac`…）；再按 `field_rules[].value_map` 改写状态值。
2. **行过滤**：`row_filters` 在归一化之后求值，所以按规范值书写；带 `side` 的过滤只作用于该侧。
3. **端口映射**：只作用于 `before` 侧，键用 before 侧规则归一化、值用 after 侧规则归一化。
4. **取键**：`scalar_text(value).strip()` 逐字段取值，得到 match key。

快照版本 `config_snapshot.version >= 2` 的 run 走完整流水线；`version == 1` 的历史 run 保持旧语义（过滤看原始行、键不归一化、同键按采集顺序配对），回填、检索、导出结果不变。

## 2. 规范值

比对层只认下面这些取值。设备原始值不同的，用 `value_map` 或 parser 输出规范值解决，**引擎里不写厂商特例**。

| metric | 字段 | 规范取值 |
|---|---|---|
| `interface_brief` | `admin` / `phy` / `prot` | `up` / `down`（华为 `*down` 记 `admin=down`） |
| `bgp_peer` | `state` | `established` / `idle` / `active` / `connect` / `opensent` / `openconfirm` |
| `bgp_peer` | `afi` | `ipv4` / `ipv6` / `vpnv4` / `vpnv6` / `evpn` / `vpls` |
| `arp` | `entry_type` | `dynamic` / `static` / `interface` |
| `isis_adjacency` / `ospf_neighbor` | `state` | `up` / `full` / `2way` / `down` / `init` / `idle` |
| `vrrp` | `af` | `ipv4` / `ipv6` |

### 中兴（已实现）现状

中兴 parser 输出设备打印的原值，没有改写：

- `interface_brief.admin/phy/prot`：`up` / `down`，已是规范值。
- `arp.entry_type`：由 `age` 推断，`HH:MM:SS` 为 `dynamic`，否则 `static`。
- `bgp_peer.state`：设备原值，如 `Established`（首字母大写）。与中兴自身对比时一致；与其他厂商对比时在模板里加值映射：

  ```json
  {"field": "state", "value_map": {"established": "established", "estab": "established", "*down": "down"}}
  ```

  `value_map` 大小写不敏感匹配，输出为右侧的规范值；两侧都经过同一张表。

> 没有改动中兴 parser 的输出值：已采集批次里的值是原值，直接改成小写会让“旧批次 vs 新批次”出现假差异。需要统一时在模板里加 `value_map`。

### 其他厂商（待办）

华为 VRP 的状态 parser（`interface_brief`、`arp`、`bgp_peer`、`isis_adjacency`、`ospf_neighbor`）**尚未实现**：仓库里没有真实输出样本，按规则不凭空编写 TextFSM。收到样本后按 `skills/biz-state/netx-biz-state/SKILL.md` 第 2 节逐项完成，输出与中兴相同的字段名，状态值输出上表的规范值，并给 profile 补 `slice`。

目前只有 LLDP 在跨厂商场景下有真实 parser。

## 3. 命令切片（`ParseProfile.slice`）

一个采集命令产出 metric 的哪一部分，由 profile 的 `slice` 声明，例如 `zte.bgp_ipv4_summary` → `{"afi": "ipv4"}`、`zte.vrrp_ipv6` → `{"af": "ipv6"}`。比对表单（sheet）用顶层 `eq` 行过滤声明自己要的切片。

- 一侧没有任何命令覆盖该切片 → 该 sheet 状态 `not_collected`，不判业务缺失、不计入通过率。
- 割接监控创建 HF 采集任务时，取两侧画像命令切片的并集；某侧找不到对应切片的 profile 时返回 `no_profile_for_slice:<metric>:<slice>:<vendor>`。
- 没声明 `slice` 的命令视为覆盖整个 metric。

## 4. sheet 数据状态

| 状态 | 条件 | 通过率 |
|---|---|---|
| `ok` | 两侧采集正常 | 正常计算 |
| `no_data` | 两侧过滤后都是 0 行，命令都成功 | `null`，不计入总通过率 |
| `suspect_parse` | 某侧 `raw_line_count > 5` 但 `row_count == 0`，或全部命令失败 | 失败行照常显示，任务级 `ok = null` |
| `not_collected` | 某侧没有任何命令（或切片）产出该 metric | `null`，不计入总通过率 |

没有任何命令回执的批次（导入或历史数据）无法判断，按正常处理。
