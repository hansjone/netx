# 割接对比（业务对比 / 割接监控）问题分析与优化计划

日期：2026-10-12。范围：网络管理 → 割接对比下的 5 个子模块：业务监控（采集）、业务对比、割接监控、对比模板、监控模板。
用途：交给 Cursor 按任务逐项实施；Claude 负责验收。

本文基于代码阅读和本地脚本验证，没有连接真实设备，也没有在真实 PostgreSQL 上执行。标了 **[已验证]** 的问题已经用脚本复现（脚本放在会话 scratchpad，结论见各条目）。基线：开始修改前，相关后端回归 **279 项通过**，`tsc -b` 通过，`cutoverDataCache` / `compareTemplateModel` Node 测试通过。

---

## 0. 结论摘要

1. **两种模式共用一套“归一化 + 映射”概念，但各个代码路径的实现不一致。** 割接监控会先对端口映射的键和值做接口归一化，业务对比不会（**[已验证]**：按设备原始名称填写映射，业务对比全部变成“删除 + 新增”）。行过滤用原始值，映射作用在归一化之后。键字段不受 `field_rules` 归一化影响。跨厂商割接最容易在这里产生误报。
2. **目前只有两种映射：接口类型别名（模板级，两侧同一套规则）和端口映射（before→after，一对一）。** 没有分侧（按厂商）归一化，没有字段值映射（状态枚举、对端 IP、VRF 改名），也无法提前检查映射覆盖率。映射漏配会直接表现为业务“缺失”，用户很难判断是映射问题还是业务问题。
3. **除 LLDP 外，状态采集解析只实现了中兴。** 华为、思科等的 parser 都是会抛出 `NotImplementedError` 的空壳，且没有注册。内置对比模板（`ZTE status default`）里的过滤和容差也都按中兴设定。所以现在的“跨厂商映射”只对 LLDP 真正有效，其余检查项在跨厂商场景下要么采集不到，要么全量误报。
4. **判定语义有几处会把正常割接判成失败：**
   - 端口预设要求旧端口 `admin=down 且 phy=down`。现场常见的做法是拔纤但不 shutdown，这种情况验收时会判为“未完成/红”（**[已验证]**）。
   - 两侧都是 0 行时通过率是 100%（空对空误判为通过）。
   - 解析不到数据会被当成业务缺失。
   - 新旧两侧的采集命令集不一致时，会被当成业务丢失。
   - 内置模板对 `output_bps` 用绝对值容差 5 bps，对 `rx_power`（dBm）用百分比容差。
5. **割接监控的运行数据无限增长。** 每个 `BizMigrationRun` 或 `BizCompareRun` 引用的采集批次都会被永久保护，run 和 diff 本身也没有保留策略。60 秒一轮的高频监控跑几天，数据库和磁盘会持续膨胀。
6. **性能：** 业务对比只要配置了端口映射，所有检查项（包括不含接口字段的 BGP 路由百万行表）都会退回 Python 引擎。割接验收是同步 HTTP 请求，会把路由表全量载入内存。
7. **引用保护有漏洞：** 被监控模板引用的对比模板、被任务或割接项目引用的端口映射都能被删除。删除后，业务对比会静默地按“无映射”执行；割接监控会直接报错停摆。

---

## 1. 现状框架梳理（给 Cursor 的背景）

### 1.1 数据流

```
业务监控任务(biz_state_task, 每台设备)
  └─ 采集批次 batch ──> 每条命令 batch_command(parse_status/row_count/raw_line_count)
        └─ 解析(ParseProfile → parser → FieldDef) ──> biz_state_metric_row(data_json) / biz_state_lldp_neighbor
                                │
对比模板(biz_compare_template.metrics_json = 多个 sheet)
  sheet: metric_id, key_fields, iface_fields, compare_fields, display_fields,
         row_filters, field_rules(normalize/compare/tolerance), ignore_port_changes
  模板级: iface_normalize_json（接口类型别名，两侧同一套）
                                │
┌───────────── 业务对比（BizCompareJob/Run/Diff）─────────────┐
│ before 批次 vs after 批次（两台设备或同一设备前后）           │
│ 行过滤(原始值) → 接口归一化(两侧) → 端口映射(仅 before)      │
│ → 按 match_key 分组 → 同键按采集顺序 zip 配对                │
│ → added/removed/changed/unchanged → 通过率                   │
│ 引擎：PG + 可下推 → SQL FULL OUTER JOIN；否则 Python          │
└─────────────────────────────────────────────────────────────┘
┌───────────── 割接监控（BizMigrationProject/Batch/Run/Diff）──┐
│ 旧设备: 画像基线 vs 高频当前   → old_kind                    │
│ 新设备: 画像基线 vs 高频当前   → new_kind                    │
│ 预期集(expect_set: 本批要迁移的端口/对象) + 监控模板(成功/异常规则)│
│ → dual_verdict: migrated/migrating/anomaly/lost/unfinished…  │
│ → 本批完成(finish_batch)=终验 → 红单(带红继续)               │
│ 高频采集：每个 metric 一对(新/旧)任务，同轮次配对采样         │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 现有“映射/归一化”层与使用点（不一致的根源）

| 层 | 归属 | 作用对象 | 业务对比 | 业务对比·实时检索/回填/导出 | 割接监控 |
|---|---|---|---|---|---|
| 接口类型别名 `iface_normalize_json` | 对比模板 | `iface_fields`，两侧同规则，只替换前缀 | 有 | 有（`_run_transform_config`） | 有 |
| 端口映射 `biz_port_mapping` | 任务 / 项目 | `iface_fields`，before→after，精确匹配 + 父接口继承 | **键/值不归一化** | 同左 | **键/值先归一化**（`service.py:1019`） |
| 行过滤 `row_filters` | sheet | **原始值**（归一化之前） | 有（PG 可下推） | 有 | 有（Python） |
| 字段规则 `field_rules.normalize` | sheet | **仅 compare 字段**，键字段不处理 | 有 | 有 | 仅完整性校验 |
| 监控模板 `sheet_overrides` | 监控模板 | 状态语义（up/down 值、成功/异常模式） | — | — | 有 |

关键文件：
- 引擎：`netx_api/biz_state/compare_engine.py`、`compare_rules.py`、`iface_normalize.py`、`compare_sql.py`
- 业务对比服务：`netx_api/biz_state/compare_service.py`（约 4000 行）
- 割接监控：`netx_api/biz_migration/evaluate.py`、`service.py`、`monitor_templates.py`、`auto_monitor.py`
- 采集：`netx_api/biz_state/collect_runner.py`、`claim.py`、`profiles.py`、`parsers/*`
- 保留：`netx_api/biz_state/retention.py`
- 前端：`web/src/features/cutover/BizComparePage.tsx`、`BizMigrationPage.tsx`、`BizMonitorTemplatesPage.tsx`、`BizStatePage.tsx`
- 内置模板：`netx_api/biz_state/data/default_zte_status_template.json`

---

## 2. 问题清单

严重度：**P0** 会产生错误结论或数据损坏；**P1** 高概率误报或明显性能/可用性问题；**P2** 边界问题或体验问题；**P3** 清理项。

### 2.1 业务对比（BizCompare）

| ID | 严重度 | 问题 | 位置 | 影响 |
|---|---|---|---|---|
| B-01 | P0 **[已验证]** | 端口映射的键/值不按模板接口归一化规则转换，必须由用户手工填写“归一化后”的名称；割接监控却会自动归一化。两种模式行为不一致。 | `compare_engine.py:224-260`（`apply_port_map` 用原始 `pmap`），`compare_service.py:2461`、`_run_transform_config:2337` | 模板有 `GE→gei` 规则时，映射 `GE0/1/0/1→gei0/2/0/1` 完全不命中，结果 added=1 / removed=1、`map_ok=False` |
| B-02 | P1 | 任务里只要有端口映射，所有 sheet 都会跳过 SQL 引擎，包括没有接口字段的 BGP/路由表 | `compare_sql.py:165`（`if port_map: return "port_map"`） | 百万行 vpnv4 路由退回 Python，内存和耗时都大幅上升；割接任务几乎都会配映射 |
| B-03 | P1 | 端口映射被删除后，业务对比静默地按“无映射”执行（还会触发自动忽略端口的启发式），删除时也不检查引用 | `delete_mapping:1603`，`_port_map_dict:1612`，`_validate_compare_job:2220` | 结果语义悄悄改变 |
| B-04 | P1 | 对比模板删除只检查 `BizCompareJob` 的引用，不检查 `BizMonitorTemplate.compare_template_id` | `delete_template:1521`，`list_templates:1391` 的 `job_count` | 割接窗口中删掉模板后，`run_evaluate` 报 `compare_template_missing`，监控停摆 |
| B-05 | P1 | 创建向导或任务配置里编辑了映射文本但没点“保存映射”，创建/保存/执行任务时映射文本被丢弃，没有任何提示 | `BizComparePage.tsx:1814 jobConfigBody`、`1895 createJob`、`1997 saveJobConfig`、`2029 runNow` | 用户以为配了映射，实际没生效，产生大量假“缺失/新增” |
| B-06 | P2 | 映射文本解析只认 `, | Tab` 作分隔符；空格分隔或格式不对的行被静默丢弃，没有解析计数或错误提示 | `BizComparePage.tsx:1720 parseMapRows` | 映射少行而不自知 |
| B-07 | P1 | 映射校验只针对“第一个含接口字段的 sheet”，结果以原始 JSON 展示；只报告“映射键未命中”，不报告“before 侧端口没有映射且在 after 侧不存在”（漏配） | `validate_mapping:1810`，`mapping_stats`（`compare_engine.py:100`），`BizComparePage.tsx:2272` | 漏配映射最终表现为业务缺失 |
| B-08 | P1 | `row_filters` 作用在原始行（归一化、映射之前）；跨厂商时，同一条过滤在两侧含义不同 | `_load_metric_rows:1619`（SQL/Python 都在加载时过滤），`_run_sheet:2179` | 例如 `interface contains gei` 会把华为 `GE0/0/1` 整侧滤掉；ARP 的 `age_timer`（HH:MM:SS）只适用于中兴 |
| B-09 | P2 | 键字段不应用 `field_rules.normalize`（如 MAC 格式），跨厂商的 MAC/大小写差异导致配对失败 | `compare_engine.py:96 row_key`，`compare_sql.py _rk_sql` | 键里含 MAC 的 sheet（如 `l2vpn_mac`）全量误报 |
| B-10 | P1 | 两侧都是 0 行时通过率为 100%；解析不到数据（命令执行了但解析 0 行）被当成业务“缺失” | `_enrich_summary:3015`，`compute_pass_rate:210` | 空对空误判为通过；模板不匹配被当成业务丢失 |
| B-11 | P1 | 新旧两侧采集命令集不一致（例如旧设备采了 bgp ipv4 summary，新设备没采），对比结果是“全部缺失” | 没有任何检查 | 采集配置问题被误判为业务问题 |
| B-12 | P2 | 同键多行按采集顺序 zip 配对，CLI 输出顺序变化会产生假“不一致” | `compare_engine.py:331+`、`compare_sql.py` 中 `dup_rn ORDER BY seq` | ECMP/多路径场景误报 |
| B-13 | P2 | 只要提供了映射，`ignore_port_changes` 就被忽略；部分映射时，未映射端口既不按名称匹配也不忽略端口 | `compare_engine.py:224` | 部分映射时策略不可控 |
| B-14 | P2 | 结果表的键列只显示映射后的名称，看不到 before 侧原始端口名 | `BizComparePage.tsx:4161`（`d.key[k] ?? pre[k]`，`pre` 取 `mapped_before`） | 跨厂商时现场人员无法对照原端口 |
| B-15 | P2 | 判定没有方向：状态 down→up、前缀数增加也算失败；流量计数器被当成严格比较项 | `compare_rules.values_equal` | 割接后业务恢复或流量迁入被判为失败 |
| B-16 | P3 | `batch_metric_collect_ok` 是死代码；`_load_metric_rows` 中 LLDP 分支重复过滤；PG 下推时 `before_raw_count` 实际上是过滤后的数量 | `compare_service.py:78`、`1675-1688`、`2199` | 清理 |
| B-17 | P3 | 防止同一任务并发对比的锁是进程内的；`_job_has_active_run` 检查和插入不是原子操作。biz_state worker 多副本时，自动对比有极小概率并发 | `compare_service.py:57`、`2887` | 低概率 |

### 2.2 割接监控（BizMigration）

| ID | 严重度 | 问题 | 位置 | 影响 |
|---|---|---|---|---|
| M-01 | P0 **[已验证]** | 端口预设的成功条件要求旧侧 `removed` 或 `admin=down 且 phy=down`。旧端口拔纤但没有 shutdown（admin up / phy down）、新端口已 up 时：窗口内是 `migrating/黄`，**验收时是 `unfinished/红`** | `monitor_templates.py:255-275`（`_success_stateful` 的 `old_down_groups` 用的是 AND） | 正常割接被判为验收失败 |
| M-02 | P1 | 每个 run 引用的 HF 批次都会被永久保护，run 和 diff 本身也没有保留策略 | `retention.py:65-75` | 高频监控期间数据库无限增长 |
| M-03 | P1 | 验收（`finish_batch`）和手动评估是同步 HTTP 请求；路由类 sheet（`bgp_route`/`ip_route`）会把 4 个批次的全量行载入 Python | `biz_migration_router.py` 的 evaluate/finish，`service.py:1148-1163` | 超时、内存暴涨；而且这期间一直持有项目行锁（`with_for_update`，`service.py:940`） |
| M-04 | P1 | 默认监控模板会采集对比模板里的全部 metric，包括只能手动采集的路由表。没有手动采集时，该 sheet 是 `current_missing`，验收**必然**失败（`current_missing` / `coverage_incomplete`） | `resolve_collect_metric_ids:525`、`finish_batch:2358-2371`、`MANUAL_ROUTE_METRICS` | 默认配置跑不通验收 |
| M-05 | P1 | 只要任意 sheet 有重复键，验收就判失败（`duplicate_keys`，同时 `coverage_complete=False`），不管这个 sheet 是否在本批范围内 | `service.py:1299`、`2366` | 例如 ISIS L1/L2 两行同键，就永远过不了验收 |
| M-06 | P1 | 新旧两侧画像任务的命令集可能不同，HF 任务沿用各自画像的命令项，旧侧有、新侧没采的切片会被判为 `lost` | `_portrait_metric_items:1741`、`ensure_highfreq:1919` | 采集配置问题被当成业务丢失 |
| M-07 | P1 | 创建项目时不校验基线批次是否属于该侧画像任务（patch 时会校验） | `create_project:686-691`（对比 `patch_project:744-750`） | 可能拿错设备的基线；直到评估时才报 `baseline_task_mismatch` |
| M-08 | P2 | 不支持同设备割接（板卡更换、同框端口迁移）：`collect_pair_same_device` 直接报错 | `service.py:2016` | 常见场景无法使用 |
| M-09 | P2 | 每个 metric、每台设备一个 HF 任务；15 个检查项就是每分钟每台设备约 15 次 SSH 登录；没有单设备并发上限 | `ensure_highfreq:1943`，`claim.py` | VTY 耗尽、AAA 日志刷屏，进而采集失败，看板大量 `current_missing` |
| M-10 | P2 | 每轮自动评估都重新载入基线全量行（基线不会变） | `run_evaluate.metric_rows:1059` | 白白消耗 CPU/IO |
| M-11 | P2 | 双侧评估里，诊断索引的键串用 `str(v)` 构造，而其他地方用 `scalar_text().strip()`：`None` 变成 `"None"`，`True` 变成 `"True"`，空白不裁剪，预期键匹配不上 | `evaluate.py:759`、`compare_engine.py:273 _key_obj` | **[已验证]**：键字段为 `None` 时，存在且未变化的对象被判为 `unfinished` |
| M-12 | P2 | 用 metric 名称包含 `bgp` / `pw` 来决定是否用端口映射推导范围（字符串启发式） | `evaluate.py:804` | 新增 metric 时行为不可预期，也违反“引擎不写 metric 特例”的原则 |
| M-13 | P2 | 映射允许 N:1 吗？业务对比允许（结果是重复键），割接监控直接报 `mapping_target_ambiguous`；1:N（拆分 breakout）两边都不支持（`before_if` 唯一） | `models/biz_state.py BizPortMappingRow`，`service.py:991/1021` | 端口汇聚、拆分割接无法表达，报错也没说明是哪几行 |
| M-14 | P3 | `project_to_dict`（GET/列表）里会写库（`resolve_project_monitor_template` 持久化默认模板）；`list_projects` 存在 N+1 | `service.py:486-503`、`543` | 只读接口有副作用 |
| M-15 | P3 | 一个键在后续验收中已经变绿，对应红单仍然保持 open，只能人工关闭 | `_persist_red_tickets_from_run:2326` | 红单堆积 |

### 2.3 内置模板与跨厂商能力

| ID | 严重度 | 问题 | 位置 |
|---|---|---|---|
| T-01 | P1 | `interface_detail.output_bps` 用 `numeric` 绝对容差 5（bps），而 `input_bps` 用 5%：相当于对出方向流量做严格比较 | `default_zte_status_template.json` |
| T-02 | P1 | `optical_brief.rx_power` 用 `percent 5`：dBm 是对数单位，-0.5 和 -0.6 已经差 20%。应改为绝对值 dB 容差 | 同上 |
| T-03 | P2 | 流量计数器（bps/util）默认参与比较；割接后流量迁移是预期行为，会产生大量噪声 | 同上 |
| T-04 | P2 | `l2vpn_mac` 键里的 `ac_port` 是接口，但没放进 `iface_fields`，端口映射不生效 | 同上 |
| T-05 | P1 | 华为、思科、华三、Juniper、诺基亚、爱立信的状态 parser 都是空壳（没有注册）；只有 LLDP 跨厂商可用 | `parsers/huawei/__init__.py` 等 |
| T-06 | P2 | 各厂商的状态值不统一（`Established`/`ESTAB`、`UP`/`up`/`*down`、`Full/DR`），而对比层只有 `lower`/`upper`/`mac` 几种归一化，没有枚举别名映射 | `compare_rules.normalize_value` |

---

## 3. 优化方案（分阶段任务，按顺序执行）

### 通用执行约束（Cursor 每个任务都要遵守）

1. **引擎里不写 metric 特例**：所有 metric 相关的行为放进模板、sheet 或 profile 声明（遵循 `skills/biz-state/netx-biz-state/SKILL.md` 第 4、5 节）。
2. **Python 和 SQL 必须一致**：凡是影响配对或判定的改动，要么两个引擎都实现，要么在 `sql_compare_skip_reason` 里让 SQL 自动降级到 Python，并补“同输入两引擎结果一致”的对照测试（SQL 侧至少断言生成的 SQL 和降级原因）。
3. **历史 run 不变**：新增的执行配置写进 `config_snapshot`，并把 `version` 升到 2。`version==1` 和旧记录的回填、检索、导出保持原行为，不能因为新逻辑改变历史结果。
4. **数据库结构**：尽量用 JSON 字段扩展，不加列。确实需要加列或表时，同时补 `alembic/versions/` 和 `netx_api/schema_patches.py`（参照现有做法），并写回归测试。
5. **每个任务**：补单元测试；跑第 5 节的回归命令；前端改动跑 `tsc -b` 和 `vite build`，涉及交互的补 `web/tests/*.browser.mjs` 场景（全部 `/v1` 用固定响应）。
6. 一个任务一个提交，提交信息写清楚任务 ID（例如 `fix(cutover): B-01 normalize port-map keys`）。

---

### 阶段 A：正确性快修（P0/P1，改动小，先做）

#### A1 端口映射键/值统一归一化（B-01）
- 新增函数 `normalize_port_map(port_map, rules) -> (normalized_map, conflicts)`，放在 `iface_normalize.py`：键和值都用 `normalize_iface_name` 处理。归一化后键冲突（两个原始键归一后相同但目标不同）要作为 conflicts 返回。
- 业务对比：在 `_run_transform_config`（`compare_service.py:2337`）统一处理，覆盖执行、实时检索、回填、导出所有路径。**只对 `config_snapshot.version>=2` 的 run 生效**；`version==1` 和旧记录保持原样。
- 割接监控：`run_evaluate` 和 `list_baseline_expect_objects` 里的内联归一化改为调用这个函数，消除重复实现。
- `mapping_stats` 的 `hint` 文案去掉“必须填写归一化后名称”。
- **验收测试**：模板规则 `GE→gei`、映射写原始名 `GE0/1/0/1→gei0/2/0/1`，新 run 的结果是 `unchanged=1`；同样的数据在 `version=1` 的 run 上回填，结果不变。

#### A2 引用保护和缺失检测（B-03、B-04）
- `delete_mapping`：被 `BizCompareJob.mapping_id` 或 `BizMigrationProject.mapping_id` 引用时返回 409 `mapping_in_use`，detail 里带引用列表（任务名/项目名）。
- `delete_template`：同时检查 `BizMonitorTemplate.compare_template_id`，返回 409 `template_in_use`（detail 区分 job/monitor）。`list_templates` 增加 `monitor_template_count`。
- `_validate_compare_job`：`job.mapping_id` 非空但映射不存在时返回 400 `mapping_missing`。
- 前端：删除失败时显示引用对象；模板列表显示两类引用数。
- **验收测试**：上述 3 种 409/400 的单测；被引用的映射无法删除。

#### A3 映射文本不会丢失（B-05、B-06）
- `parseMapRows` 改为返回 `{rows, invalidLines[]}`：两段文本即使用空格分隔也接受（正好两个 token 时）；重复的 before 行要提示。
- 映射文本相对于已保存版本“有修改”时：创建任务、保存配置、立即执行都要先拦截，提供“保存映射并继续 / 放弃修改”两个选项（或者自动先保存映射再保存任务，二选一，推荐自动保存并提示）。
- 编辑区下方实时显示“已解析 N 对 / 无效 M 行（行号）”。
- 保存一个被其他任务或项目共用的映射时，提示“该映射被 X 个任务 / Y 个割接项目使用”（后端 `list_mappings` 返回引用计数）。
- **验收（浏览器脚本）**：在向导里输入映射后直接点“创建”，请求体里的 `mapping_id` 非空，或者被拦截并出现提示；空格分隔的行能被解析；无效行有提示。

#### A4 端口预设成功条件修正（M-01）
- `preset_override_for_metric("interface_brief")` 中旧侧的条件改为三个 OR 组：`[removed]`、`[phy in down]`、`[admin in down]`；新侧保持 `admin up 且 phy up`。
- 已存在的监控模板：在 `ensure_default_monitor_templates` 中增加一个幂等升级，**只替换结构上和旧预设完全一致的 override**（逐字段对比），用户自定义过的不动，并在 `override_warnings` 里提示“端口成功条件为旧版 AND 语义”。
- **验收测试**：新增 4 个用例——旧侧 admin up/phy down、admin down/phy up、两者都 down、端口已删除；在窗口内和验收时，新侧 up 都应判为 `migrated/green`。另外，新侧 phy down 时仍判为红。

#### A5 创建项目时校验基线归属（M-07）
- `create_project` 复用 `patch_project` 的校验：基线批次的 `task_id` 必须等于对应侧的画像任务，状态必须是 `success` 或 `partial`。抽成公共函数 `_validate_baseline_ownership`。
- **验收测试**：拿其他设备的批次作为基线创建项目，返回 400 `old_baseline_invalid` / `new_baseline_invalid`。

#### A6 键串规范化统一（M-11）
- 在 `compare_engine.py` 新增 `key_tuple(row, fields)`，统一用 `scalar_text(...).strip()`。`_key_obj` 的输出键值和 `build_diff_index_from_compare` 都改用它（`evaluate.py:759` 不再用 `str(v)`）。
- **验收测试**：键字段为 `None`、`True`、带首尾空格时，预期键能命中，判定为 `ok`/`migrated` 而不是 `unfinished`。

#### A7 内置模板参数修正（T-01、T-02、T-04）
- `default_zte_status_template.json`：
  - `output_bps` 改为 `percent 5`（和 `input_bps` 对称）。
  - `rx_power` 改为 `numeric`，`tolerance: 2`（单位 dB）。
  - `l2vpn_mac.iface_fields` 加上 `ac_port`。
  - `interface_detail` 的 `input_bps/output_bps/in_util/out_util` 移出 `compare_fields`，只保留在 `display_fields`（见 V3 的方向性规则；在 V3 完成之前先不比较）。
- `ensure_default_zte_status_template` 增量升级：**只修改值仍等于旧打包值的字段**，用户改过的保留。
- **验收测试**：旧打包值的模板升级后变成新值；用户自定义过 `rx_power` 规则的模板升级后保持不变。

---

### 阶段 B：统一的“身份归一化流水线”（跨厂商核心）

目标：业务对比执行、实时检索、回填、导出、映射校验、割接评估、预期集选择器这 7 条路径，**对同一行算出相同的 match key**，并支持按侧（按厂商）的规则和字段值映射。

#### B1 抽出 `IdentityPipeline`（解决“各路径各写一遍”）
- 新文件 `netx_api/biz_state/identity_pipeline.py`：
  ```python
  @dataclass(frozen=True)
  class SideSpec:
      iface_rules: list[dict]       # 该侧接口归一化（已合并 both + side）
      value_maps: dict[str, dict]   # field -> {from_lower: to}
  @dataclass(frozen=True)
  class Pipeline:
      before: SideSpec; after: SideSpec
      port_map: dict[str, str]      # 已归一化（A1）
      key_fields, iface_fields, compare_fields, field_rules, row_filters, match_keys
      def transform(self, row, side) -> dict      # 归一化 → 值映射 →（before 侧）端口映射
      def match_key(self, row, side) -> tuple
      def keep(self, row, side) -> bool           # 行过滤（在 transform 之后求值，见 B4）
  ```
- 由 `build_pipeline(sheet, snapshot_or_template, port_map, before_vendor, after_vendor)` 统一构造；`compare_rows`、`_live_search_sheet_diffs`、`_load_search_key_groups`、`_hydrate_diff_rows`、`validate_mapping`、`evaluate_metric_dual`、`list_baseline_expect_objects` 全部改用它。
- **验收测试**：参数化对照测试，用同一份 fixture 断言 7 条路径对同一行给出的 match key 相同（重点覆盖：映射、父接口继承、QinQ、值映射、归一化规则）。

#### B2 分侧接口归一化 + 厂商内置规范化
- `iface_normalize_rules` 每条增加可选字段 `side: "both"|"before"|"after"`（默认 `both`）和 `vendor`（可选，按批次的厂商自动生效）。前端模板编辑器支持选择。
- 内置“厂商内全称/缩写”规范化表（`iface_normalize.py` 中的 `VENDOR_CANONICAL`）：
  - 华为：`GigabitEthernet`↔`GE`、`XGigabitEthernet`↔`XGE`、`100GE`、`Eth-Trunk`
  - 思科：`GigabitEthernet`↔`Gi`、`TenGigE`↔`Te`、`HundredGigE`↔`Hu`、`Bundle-Ether`↔`BE`
  - 华三：`GigabitEthernet`↔`GE`、`Ten-GigabitEthernet`↔`XGE`、`Bridge-Aggregation`↔`BAGG`
  - 中兴：大小写
  - 统一去掉名称中的空格。

  只做厂商内同义统一，**不做跨厂商编号转换**（那是端口映射的职责）。模板 sheet 可以用 `builtin_vendor_canonical: false` 关闭。
- 批次的厂商取 `BizStateBatch.vendor`，取不到再用任务的 vendor，经 `resolve_vendor_key` 解析。
- `config_snapshot` v2 记录两侧最终生效的规则。
- **验收测试**：华为 `GigabitEthernet0/0/1` 与 `GE0/0/1` 互认；before 华为、after 中兴，只对 before 生效的规则不影响 after。

#### B3 字段值映射（T-06、B-09）
- `field_rules[]` 新增可选字段：
  - `value_map: {"estab": "established", "*down": "down", ...}`（大小写不敏感匹配，输出规范值）
  - `side: both|before|after`
  - `apply_to_key: true|false`（默认：字段在 key_fields 中时为 true）
- `normalize` 的处理顺序：`normalize`（strip/lower/mac…）→ `value_map`。**键字段也要应用**（解决 MAC 作为键时跨厂商对不上的问题）。
- SQL：`value_map` 作用在键或比较字段上时，`sql_compare_skip_reason` 返回 `value_map`，降级到 Python（以后可以用 `CASE` 下推，本期不做）。
- `compare_validation.py` 校验 `value_map` 必须是 `str→str` 映射，并且每个 sheet 最多 500 项。
- 模板编辑器：字段规则行增加“值映射”编辑（每行一对，可以粘贴）。
- **验收测试**：`state: Estab` 对 `established`，配置值映射后为 unchanged；MAC 作为键字段时，`00e0-fc12-3456` 与 `00e0.fc12.3456` 能够配对；Python 与 SQL 降级原因断言。

#### B4 行过滤在归一化之后求值（B-08）
- 新 run（snapshot v2）：先 `transform` 再 `row_filters`。
- PG 下推只在“过滤涉及的字段都不在 `iface_fields` 中、也没有值映射”时启用；否则 Python 过滤。
- 支持过滤条件带 `side`（只作用于某一侧），满足“旧侧只看 dynamic ARP”这类需求。
- 模板编辑器提示“过滤在归一化之后执行，请按规范值书写”。
- **验收测试**：规则 `GE→gei` 加过滤 `interface contains gei`，before 侧华为 `GE0/0/1` 不再被滤掉；`version=1` 的 run 回放结果不变。

#### B5 端口映射覆盖率预检（B-07、B-11、M-06 的基础）
- 新接口：`POST /v1/biz-state/compare/preflight`，请求体 `{template_id, enabled_sheet_ids, mapping_id | mapping_rows, before_batch_id, after_batch_id}`。割接监控复用同一个服务函数：`/v1/biz-migration/projects/{id}/preflight`，传入基线和最新 HF 批次。
- 按 sheet 返回：
  - `before_count / after_count`（过滤后）、`parse` 状态（见 V1）
  - `mapped_exact[]`、`mapped_by_parent[]`、`identity_same_name[]`（未映射但两侧同名）
  - **`suspect_unmapped[]`**：before 侧接口没有映射，after 侧也不存在 → 疑似漏配
  - `unused_map_keys[]`、`map_targets_missing_on_after[]`、`n_to_1_targets[]`
  - `command_coverage`：两侧执行过的 profile_id 集合和差集（见 V2）
- 汇总：映射覆盖率 = 已映射 / (已映射 + 疑似漏配)，以及“可执行 / 有风险”的结论。
- 业务对比 run 的 summary 也写入同结构的 `mapping_coverage`（按 sheet），结果页可以查看。
- 前端：映射块里的“校验”按钮改为显示预检表格（取代原始 JSON）；疑似漏配可以一键加入映射编辑区（目标留空，待填）。
- **验收测试**：构造 3 个端口，分别是精确映射、父接口继承、漏配，预检分类正确；`mapping_rows` 未保存时也能预检。

#### B6 SQL 引擎条件放宽（B-02）
- `sql_compare_skip_reason`：`port_map` 只在 `iface_fields ∩ (key_fields ∪ compare_fields)` 非空时才阻止 SQL。
- **验收测试**：有端口映射的任务，`bgp_route` sheet（`iface_fields=[]`）走 SQL（断言 skip reason 为空），`interface_brief` 仍走 Python。

#### B7 映射语义：N:1 与部分映射（B-13、M-13）
- 决策见第 6 节，默认方案：
  - **N:1**：允许，但预检和 summary 中必须列出；业务对比按重复键规则处理（V4）；割接监控继续拒绝，但错误信息里列出冲突行。
  - **1:N（breakout）**：本期不支持，在映射编辑器里检测到重复 before 时明确报错，不再只报 `duplicate_before_if`。
  - **部分映射**：新增 sheet 选项 `unmapped_port_policy: "by_name"|"ignore_port"`（默认 `by_name`，即当前行为），让 `ignore_port_changes` 与映射可以组合使用。
- **验收测试**：N:1 在预检中出现在 `n_to_1_targets`；割接监控的报错包含冲突对。

---

### 阶段 C：判定语义与数据可信度

#### V1 sheet 级数据可信度（B-10）
- `_run_sheet` 和割接评估为每侧补充 `collect_quality`：该 metric 的命令数、`parse_status` 计数、`row_count`、`raw_line_count`、`declared_total`（只读这些列，不读 `raw_text`）。
- 新增 sheet 状态：
  - `no_data`：两侧过滤后都是 0 行，且命令都执行成功 → `pass_rate=null`，不计入总通过率，卡片显示“无数据”。
  - `suspect_parse`：某侧 `raw_line_count > 5` 但 `row_count == 0` → 卡片显示“疑似解析失败”，失败行数照常显示，但给出明确提示。
  - `not_collected`：某侧没有任何命令产生该 metric → 不做判定，`pass_rate=null`。
- 任务级 `ok`：任一 sheet 处于 `not_collected` / `suspect_parse` 时为 `null`（未判定），不再是 `true`。
- 前端卡片和 CSV `_summary.csv` 增加状态列。
- **验收测试**：0 对 0 → `no_data`；before 1000 行、after 有原文但 0 行 → `suspect_parse`；after 侧无命令 → `not_collected`。

#### V2 采集命令集对齐（B-11、M-06）
- `ParseProfile` 增加可选字段 `slice: dict[str, str]`（例如 `zte.bgp_ipv4_summary` 的 slice 是 `{"afi": "ipv4"}`），声明这个 profile 产出哪个切片。先给中兴的 `bgp_peer` / `bgp_route` / `vrrp` / `isis` profile 补齐。
- 判断一个 sheet 是否“被某侧覆盖”：该侧存在 `metric_id` 相同的成功命令，且（sheet 的 row_filters 里有 `eq` 条件时）存在 slice 与之相符的 profile。
- 业务对比的预检（B5）和执行时都做这个检查：未覆盖的 sheet 标记 `not_collected`（V1）。
- 割接监控：`ensure_highfreq` 生成 HF 任务项时，取**两侧画像命令项的并集**（按 profile 的 slice 转换成该侧厂商可用的 profile；找不到时返回明确错误 `no_profile_for_slice:<metric>:<slice>:<vendor>`）。评估时，未覆盖的 sheet 标记为 `not_collected`，不判红。
- **验收测试**：旧画像有 ipv4 + vpnv4，新画像只有 vpnv4 → HF 任务两侧都有 ipv4 + vpnv4；如果强制只有一侧有，评估结果是 `not_collected`，而不是 `lost`。

#### V3 方向性判定（B-15、T-03）
- `field_rules.compare` 新增：
  - `not_worse`：配合 `rank: ["up", "down"]`（排在前面表示更好）；after 不差于 before 即通过。
  - `numeric_min`：配合 `tolerance`（百分比），`after >= before*(1-tol%)` 即通过（适用于前缀数、流量“不应明显下降”）。
  - `numeric_max`：对称方向。
- `explain_diff` 输出方向原因，例如 `worse: up→down`、`drop 37% > 10%`。
- SQL：先降级（skip reason 为 `directional_compare`），后续再下推。
- 内置模板：`interface_brief.prot`、`bgp_peer.state` 默认仍为 `eq`（保守）；在模板编辑器里提供这些新模式供选择；流量计数器若要比较，推荐用 `numeric_min`。
- **验收测试**：`down→up` 用 `not_worse` 判为通过，`up→down` 判为失败；前缀数 1000→1200 用 `numeric_min 5%` 判为通过，1000→900 判为失败。

#### V4 重复键配对：先排序再配对（B-12）
- Python：同一个 match key 下，两侧各自按“归一化后的 compare 字段元组, 原始 seq”排序，然后 zip。
- SQL：`dup_rn` 改为 `row_number() OVER (PARTITION BY rk ORDER BY <compare 字段归一化表达式...>, seq, id)`。
- 用 sheet 选项 `duplicate_pairing: "sorted"|"ordered"` 控制，默认 `sorted`，只对 snapshot v2 生效；v1 回放保持 `ordered`。
- **验收测试**：同键两行的值 `[a, b]` 对 `[b, a]` → 2 个 unchanged（原来是 2 个 changed）；Python 和 SQL 生成 SQL 的断言。

#### V5 原始身份展示（B-14）
- diff 增加 `raw_key_before`（before 原始键值，未归一化/映射）。实现方式：在 Python 路径生成；对精简行，在回填时由 `IdentityPipeline` 反推；SQL 路径没有映射，原始键等于 key。
- 结果表：当原始值与映射后的值不同时，键单元格显示 `cgei-0/2/0/1 ← GE0/0/1`；CSV 增加 `<field>__raw_before` 列（只针对接口键）。
- **验收（浏览器脚本）**：映射行同时显示两侧端口名。

---

### 阶段 D：割接监控的健壮性与性能

#### H1 run、diff 保留策略 + 批次保护收敛（M-02）
- 新配置项：`biz_migration_auto_run_keep`（默认每个 batch 保留 300 个 auto run）、`biz_compare_auto_run_keep`（默认每个 job 保留 50 个 auto run）。
- 清理规则：
  - 永久保留：`purpose in (acceptance, manual)` 的 run，以及被红单 `run_id` 引用的 run。
  - 其余 auto run：超过数量上限的删除（连同其 diff）。
  - 保护范围：`retention.protected_batch_map` 只保护“仍然存在的 run”所引用的批次，并**加上 `summary.metric_batches` 里的所有批次**（目前只保护首个 binding 的批次）。
- 清理挂在 `biz_state_scheduler._maybe_purge_retention` 上，每小时执行一次。
- **验收测试**：插入 400 个 auto run 和 1 个验收 run，清理后剩 300 + 1；被删除 run 所引用的 HF 批次不再受保护，能按 `retention_days` 被清理；验收 run 引用的所有批次（包括 `metric_batches`）都受保护。

#### H2 评估和验收改为后台执行（M-03）
- 仿照业务对比的 `enqueue_compare`：`POST /batches/{id}/evaluate` 和 `/finish` 返回 `{run_id, status: "running"}`，在后台线程里执行，`BizMigrationRun.status` 依次经过 `running/success/failed`，`summary.progress` 记录进度。
- 项目行锁只在“写 run 和 diff”这个短事务里持有，不再覆盖整个计算过程。
- 重启恢复：参照 `recover_interrupted_compares_on_startup`，把遗留的 `running` 标记为 `cancelled`。
- 前端：轮询 run 的状态；验收按钮在执行期间不可重复点击。
- **验收测试**：评估请求立即返回；并发两次评估，第二次返回 409；模拟重启后状态被正确恢复。

#### H3 路由类 sheet 的处理（M-04）
- 默认：`MANUAL_ROUTE_METRICS` 对应的 sheet **不参与验收门槛**。没有在本批窗口内手动采集时，显示为 `manual_pending`（灰色），不计入 `current_missing` 或 `coverage_incomplete`；已采集时参与判定。
- 路由 sheet 的双侧比对改为复用业务对比的 SQL 路径（旧基线 vs 旧当前、新基线 vs 新当前，各执行一次 `run_sql_sheet_compare`，只取计数和失败行），避免在 Python 里载入百万行。
- **验收测试**：默认监控模板、没有采集路由时，验收不因路由失败；采集了路由时，判定会出现在结果里。

#### H4 基线行缓存（M-10）
- 进程内 LRU：键为 `(batch_id, metric_id)`，只缓存基线批次（基线不可变），总行数上限可配置（默认 50 万行）。基线变更或项目删除时失效。
- **验收测试**：连续两次 auto 评估，第二次不再查询基线行（mock 计数）。

#### H5 重复键的处理口径（M-05）
- `duplicate_keys` 只在“本批范围内的键（expect）有重复”时作为验收失败原因；其他 sheet 的重复键作为警告，写进 `accept_summary.warnings`。
- 卡片上展示具体是哪些重复键（已有 `duplicate_key_list`），并提示“完善 key_fields（例如 ISIS 加上 `lev`）或在 sheet 上启用 `duplicate_pairing: sorted`”。
- **验收测试**：范围外的 sheet 有重复键时验收可以通过；范围内有重复时验收失败。

#### H6 HF 采集合并与单设备并发上限（M-09）
- 决策见第 6 节，默认：**同间隔、同 lane（light/heavy）的 metric 合并为一对任务**（一次 SSH 采多条命令）；heavy 和 manual 的路由表保持独立。
- 新配置项 `biz_state_max_sessions_per_ne`（默认 2）：`claim_queued_batches` 认领时，同一个 `ne_id` 正在运行的批次达到上限就跳过（成对认领时要两侧都满足）。
- `_hf_bindings` 的结构保持兼容（`metric_ids` 本来就是列表）。
- **验收测试**：15 个 light metric、同一间隔 → 每侧 1 个 HF 任务；单设备并发上限生效（3 个同 NE 批次只认领 2 个）。

#### H7 同设备割接模式（M-08）
- 当 `old_ne == new_ne` 时允许创建项目：每个 metric 只建一个 HF 任务，新旧两侧共用同一批次（`collect_group_id` 只含一个任务，轮次天然对齐），映射表达同框端口迁移。
- `evaluate_metric_dual` 不需要改（两侧是同一份行数据，靠映射区分新旧端口）。
- `enqueue_collect_group` 允许单任务组（`mode=single_ne`）。
- **验收测试**：同设备项目可以创建；端口 A 迁移到 B（A 变为 down、B 变为 up）判为 `migrated`。

#### H8 去掉 metric 名称启发式（M-12）
- `sheet_overrides` 增加 `scope_by_port_map: true|false`；不填时按当前规则推导，并把推导结果写进 override 的 `effective_*` 字段供界面展示。`_map_defines_iface_expect` 不再读取 metric 名称。
- **验收测试**：一个名称不含 bgp 的 sheet 设置 `scope_by_port_map=false` 后，不会被映射推导出范围。

#### H9 其他（M-14、M-15）
- GET 接口不写库：`project_to_dict` 中的默认模板绑定改为只读解析，持久化只在 create/patch 时进行。
- 红单：在后续验收中已转绿的键标记为 `resolvable=true`（不自动关闭），前端提供“一键关闭可关闭红单”。

---

### 阶段 E：跨厂商解析（需要你提供真实样本）

#### P1 华为 VRP 首批状态解析（T-05）
- **前提**：每个命令提供至少 2 份真实输出样本，放到 `tests/fixtures/huawei/`，并做脱敏（不同版本、带子接口和 VRF 的各一份）。**没有样本时 Cursor 不得凭空编写 TextFSM。**
- 顺序：`interface_brief`（`display interface brief`）、`arp`（`display arp all`）、`bgp_peer`（`display bgp peer` / `display bgp vpnv4 all peer`）、`isis_adjacency`（`display isis peer`）、`ospf_neighbor`（`display ospf peer brief`）。
- 每个 metric 输出**与中兴相同的字段名**（FieldDef 共用），状态值输出**规范枚举**：
  - 接口 `admin/phy/prot ∈ {up, down}`；华为的 `*down` 输出为 `admin=down`
  - BGP `state ∈ {established, idle, active, connect, opensent, openconfirm}`
  - ARP `entry_type ∈ {dynamic, static, interface}`
  
  这样对比层的过滤和预设不需要按厂商分叉。
- 按 `skills/biz-state/netx-biz-state/SKILL.md` 第 2 节逐项完成：TextFSM、`prefer_fsm`、注册、profile（附 `slice`）、单测。
- 新增“规范值契约”文档：`docs/biz-state-canonical-fields.md`，按 metric 列出字段、取值范围，以及各厂商原始值到规范值的对照。中兴 parser 也要对照这份契约补齐规范化（例如 `entry_type`）。
- ARP 过滤：内置模板从 `age_timer` 改为 `entry_type eq dynamic`（依赖各厂商 parser 输出规范的 `entry_type`）；中兴 parser 在 `entry_type` 为空时，根据 age 推断出 `dynamic`。
- **验收**：fixture 单测；中兴→华为的跨厂商对比用例（固定行数据）在配置映射和值映射后通过率为 100%。

---

### 阶段 F：前端体验（和上面各任务一起做）

1. 映射编辑器升级为表格模式（保留文本模式）：两列可编辑，支持粘贴 CSV/Excel，逐行显示校验状态（重复、无效、归一化后的值）。“从基线生成”：左列 before 端口、右列 after 端口，可按“相同槽位/端口号”自动推荐。
2. 预检结果表（B5）：疑似漏配、未使用的键、N:1、命令覆盖差集，每项都可以展开查看明细。
3. sheet 卡片显示 `no_data` / `suspect_parse` / `not_collected` / `manual_pending` 状态和原因（V1、H3）。
4. 结果表的键列显示原始端口名（V5）。
5. 模板编辑器：接口归一化规则增加 side/vendor 选择；字段规则增加值映射和方向性比较；过滤条件可选 side；行过滤处提示“按归一化后的值书写”。
6. 割接看板：重复键警告、未覆盖切片、预设语义说明（端口成功条件）。

---

## 4. 建议执行顺序

| 顺序 | 任务 | 说明 |
|---|---|---|
| 1 | A1–A7 | 正确性快修，相互独立，可以并行 |
| 2 | B1 → B2 → B3 → B4 | 流水线是 B2–B4 的基础，必须先做 |
| 3 | B5、B6、B7 | 预检依赖流水线 |
| 4 | V1 → V2 → V3 → V4 → V5 | V2 依赖 V1 的状态定义 |
| 5 | H1 → H2 → H3 → H4 → H5 → H6 → H7 → H8 → H9 | H1/H2 优先（生产风险最大） |
| 6 | 阶段 E | 等你提供样本 |
| 7 | 阶段 F | 随各任务交付；最后做一次整体走查 |

---

## 5. 验收方式（Claude 验收时执行）

### 5.1 回归命令
```powershell
# 项目根目录：割接对比相关后端回归（基线 279 项通过）
.venv\Scripts\python.exe -m pytest tests/test_biz_state_compare.py tests/test_biz_state_compare_sql.py tests/test_biz_state_compare_search.py tests/test_biz_state_compare_snapshot.py tests/test_biz_state_compare_export.py tests/test_biz_state_compare_recovery.py tests/test_biz_compare_templates.py tests/test_biz_migration_evaluate.py tests/test_biz_migration_evidence.py tests/test_biz_migration_hf_decouple.py tests/test_biz_migration_integrity.py tests/test_iface_normalize.py tests/test_biz_state_monitor_service.py tests/test_biz_state_paired_sampling.py tests/test_biz_state_retention.py -q
# 全量
.venv\Scripts\python.exe -m pytest -q

# web 目录
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
node --experimental-strip-types --test tests/cutoverDataCache.test.mjs tests/compareTemplateModel.test.mjs
# 浏览器（先启动 vite :5179）
node tests/bizCompare.browser.mjs
node tests/bizCompareTemplates.browser.mjs
node tests/bizMigration.browser.mjs
node tests/bizState.browser.mjs
```

### 5.2 必须新增的测试文件（便于验收定位）
- `tests/test_identity_pipeline.py`：B1 的 7 路径对照、B2、B3、B4
- `tests/test_compare_preflight.py`：B5、B7、V2
- `tests/test_compare_quality.py`：V1、V3、V4
- `tests/test_biz_migration_presets.py`：A4 的 4 个端口场景、H5、H8
- `tests/test_biz_migration_retention.py`：H1
- `tests/test_biz_migration_async.py`：H2、H3
- `tests/test_cutover_references.py`：A2、A5
- 浏览器：`bizCompare.browser.mjs` 增加 A3、B5、V5 场景；`bizMigration.browser.mjs` 增加 H2、V1 卡片状态场景

### 5.3 我会逐条核对的场景
1. 原始名称映射（A1）：`GE0/1/0/1→gei0/2/0/1` 加规则 `GE→gei`，结果为 unchanged。
2. 拔纤但未 shutdown（A4）：验收判 `migrated`。
3. 删除被引用的映射或模板（A2）：返回 409。
4. 向导中映射未保存（A3）：不会被静默丢弃。
5. 跨厂商过滤（B4）：华为侧不会被 `contains gei` 整侧滤掉。
6. MAC 作为键（B3）：不同格式能配对。
7. 空对空（V1）：显示 `no_data`，不是 100%。
8. 命令集不一致（V2）：显示 `not_collected`，不是 `lost`。
9. 同键乱序（V4）：不再出现假“不一致”。
10. 有映射时 BGP 路由走 SQL（B6）。
11. auto run 被清理、被引用批次的保护随之释放（H1）。
12. 验收异步执行、不阻塞请求（H2）；默认模板不因路由未采集而验收失败（H3）。
13. 历史 run（snapshot v1）的检索、回填、导出结果与改动前一致。

---

## 6. 需要你确认的决策（Cursor 按“默认”执行，除非你另行指定）

| # | 决策 | 默认 |
|---|---|---|
| D1 | 业务对比是否允许 N:1 映射 | 允许，但在预检和结果中明确列出；割接监控继续拒绝并说明冲突行 |
| D2 | 1:N（端口拆分 breakout）本期是否支持 | 不支持，明确报错 |
| D3 | 空对空 / 疑似解析失败时，任务级“是否通过” | `null`（未判定），不计入通过率，不当作失败 |
| D4 | 路由表是否参与割接验收门槛 | 未采集时不参与（`manual_pending`），采集后参与 |
| D5 | HF 采集是否按“同间隔 + 同 lane”合并任务 | 合并；每台设备并发 SSH 上限默认 2 |
| D6 | 是否支持同设备割接 | 支持（H7） |
| D7 | auto run 保留数量 | 割接每批 300、业务对比每个任务 50；验收和手动 run 永久保留 |
| D8 | 流量计数器默认是否参与比较 | 不参与；需要时用 `numeric_min` |
| D9 | 华为样本由谁提供、首批覆盖哪些命令 | 由你提供；首批为接口、ARP、BGP、ISIS、OSPF |
