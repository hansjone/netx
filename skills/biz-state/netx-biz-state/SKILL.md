---
name: netx-biz-state
description: >-
  Adds or extends NetX biz_state status metrics: TextFSM rules, vendor parsers
  (FSM-first prefer_fsm), ParseProfile FieldDef, multi aux_commands, EnrichJoin
  cross-command joins, CollectSession cache. Use when adding show/monitor
  commands, ARP/VRF-style enrich, cli_templates, or biz_state parsers/profiles.
---

# netx-biz-state（状态监控采集 / 解析）

在 **netx** 仓库为业务状态监控加一项命令或跨命令补字段时，先读本 skill 再改代码。

**原则：所有命令解析先 TextFSM；无行再手写。** 禁止一上来只写 regex 当主路径（除非确认无法写模板）。

---

## 1. 架构速览

```
ParseProfile (命令 + schema + aux + enrich)
    → CollectSession（同批命令缓存）
    → apply_rules → fsm_tables
    → normalize (prefer_fsm)
    → EnrichJoin（等值拷字段）
    → metric rows / compare
```

| 路径 | 作用 |
|------|------|
| `netx_api/cli_templates/` + `index` | TextFSM 规则（stem = rule key） |
| `netx_api/ntc_parse.py` | `apply_rule` / `apply_rules` / `rules_for_command` |
| `netx_api/biz_state/parsers/<vendor>/` | `RULE_KEYS` + `normalize` + `prefer_fsm` |
| `netx_api/biz_state/profiles.py` | `ParseProfile` / `AuxCommand` / `EnrichJoin` / `FieldDef` |
| `netx_api/biz_state/collect_session.py` | 辅命令解析、缓存、`run_primary_with_bundle` |
| `netx_api/biz_state/enrich.py` | 声明式等值 join |
| `netx_api/biz_state/collect_runner.py` | 任务会话采集落库 |

样板：`zte/interface_brief`（单命令）、`zte/if_intf`（可复用辅表）、`zte.arp`（主+辅+enrich）。

---

## 2. 加「单命令」状态指标（常见）

按序做完，缺一不可（对比 UI 需要 FieldDef）：

1. **TextFSM**  
   - 文件：`cli_templates/<vendor>/<platform>_<stem>.textfsm`  
   - `cli_templates/index` 一行：`path, .*, <platform>, <cmd-abbrev>`  
   - stem 例：`zte_zxros_show_arp`

2. **Parser** `parsers/<vendor>/<metric>.py`  
   ```python
   RULE_KEYS = ("zte_zxros_show_xxx",)

   def normalize_xxx(*, raw_text, fsm_tables=None, vendor="", device_type="", command="", params=None, **_):
       tables = dict(fsm_tables or {})
       if not any(tables.get(k) for k in RULE_KEYS):
           # 可选：本地补跑 apply_rules，便于单测直调
           ...
       return prefer_fsm(tables, RULE_KEYS, _map_fsm, _hand, raw_text=raw_text, ...)

   normalize_xxx.RULE_KEYS = RULE_KEYS
   ```
   - `_map_fsm`：FSM 行 → 与 FieldDef 同名的 dict  
   - `_hand`：仅 FSM 无行时兜底  

3. **注册** `parsers/<vendor>/__init__.py` → `PARSERS["xxx"] = normalize_xxx`  
   - 若 `metric_id` 走通用落库，把 id 加入 `collect_runner._GENERIC_METRICS`

4. **Profile** `profiles.py`  
   ```python
   ParseProfile(
       profile_id="zte.xxx",
       vendor_key="zte",
       metric_id="xxx",
       parser_id="xxx",
       command_template="show ...",
       match=r"(?i)^\s*show\s+...\s*$",
       textfsm_command="show ...",
       fields=[FieldDef(..., is_key=True / role="state"|"meta")],
       kind="collect",
   )
   ```

5. **单测**  
   - FSM：`apply_rule(platform=..., rule_key=stem, text=sample)` 非空  
   - `run_parser("xxx", ...)` 字段正确  
   - 有 `test/log` 则截取真实片段

---

## 3. 加「跨命令」补字段（多辅命令）

辅命令也按 **§2 完整状态指标** 开发（可单独采集、可复用）。

主 profile 只声明绑定与 join（**不要**在主 `normalize` 里硬编码辅表拼装，除非 join 表达不了）：

```python
aux_commands=[
    AuxCommand(key="if_intf", profile_id="zte.if_intf"),
    # 多辅：再 append；key 唯一
],
enrich_joins=[
    EnrichJoin(from_aux="if_intf", on="interface", take=("vrf",)),
    # on= 左右同字段；或 left_on= / right_on=
],
```

- `AuxCommand`：**仅** `key` + `profile_id`（命令 / parser / RULE_KEYS 从辅 profile 读）  
- `EnrichJoin`：主表 `normalize` 之后，按键等值拷贝 `take` 字段；未命中且 `fill_missing` 则置 `""`  
- 同批相同 concrete CLI 由 `CollectSession` 缓存，不重采不重解析  

复杂拼装（多键、聚合、推算）才在 `normalize` 里读 `raws` / `aux_records` / `fsm_tables`。

---

## 4. 禁止 / 注意

- 不要把整任务「先采完再解析」当默认；保持 **一项内主+辅连采 → 立刻 normalize**  
- 不要自动从 TextFSM 列生成对比 schema（FieldDef 手写）  
- 辅命令不要再抄一份 `command_template`/`rule_keys` 到 `AuxCommand`  
- `parser_id` 全局扁平：新指标命名避免跨厂家撞名；注册顺序后写覆盖先写  
- TextFSM 字面量 `$`：不要用 `^\$`（模板替换冲突）；能靠下一 `interface` 切块则不匹配 `$`

---

## 5. 自检清单

- [ ] index + apply_rule 对样本有行  
- [ ] prefer_fsm：有 FSM 不走手写；无 FSM 手写仍通  
- [ ] Profile match 能命中任务里的 concrete 命令  
- [ ] 跨表：辅 profile 可单独 `run_parser`；主 profile enrich 后字段正确  
- [ ] `python -m unittest tests.test_fsm_parser_pipeline tests.test_enrich_framework tests.test_multi_command_arp`（及本指标单测）

---

## 6. 参考文件

- Enrich：`biz_state/enrich.py`  
- Session：`biz_state/collect_session.py`  
- ARP+VRF：`profiles.py` → `zte.arp`；`parsers/zte/arp.py`；`parsers/zte/if_intf.py`  
- 模板：`cli_templates/zte/zte_zxros_show_arp.textfsm`、`..._if_intf.textfsm`
