# NetX CLI TextFSM templates

Custom TextFSM templates that complement (and can **override**) community `ntc-templates`.
Organize by **vendor folder**; prefer adding templates here for every new CLI parse path.

## Layout

```text
cli_templates/
  index                 # global index (paths relative to this dir)
  README.md
  zte/                  # ZTE ZXROS / ROSNG, ...
  cisco/                # overrides / extras beyond community cisco_*
  huawei/
  h3c/
  juniper/
```

## Lookup order

1. **This tree** (`index` → `vendor/*.textfsm`)
2. Community `ntc-templates` package

No regex CLI parsers. If both miss, the call returns empty — fix or add a template.

## Vendor coverage (blind fill from community + NetX)

| NetX vendor_key | ntc platform | LLDP command | Port brief | Port detail | Notes |
|-----------------|--------------|--------------|------------|-------------|-------|
| `zte` | `zte_zxros` | `show lldp neighbor brief` | `show interface brief` | `show interface {if}` | NetX custom (community has iface only) |
| `huawei` | `huawei_vrp` | `display lldp neighbor` | `display interface brief` | `display interface {if}` | NetX custom (community prompt-fragile) |
| `cisco` | `cisco_ios` / `nxos` / `xr` | `show lldp neighbors detail` | `show ip interface brief` | `show interfaces {if}` | Community + NetX iface override |
| `h3c` | `hp_comware` | `display lldp neighbor-information list` | `display interface brief` | `display interface {if}` | Community |
| `juniper` | `juniper_junos` | `show lldp neighbors` | `show interfaces` | `show interfaces {if}` | Community (rates often sparse) |
| `nokia` | `alcatel_sros` | *(stub / no SROS LLDP tpl)* | `show port` | `show port {if}` | Community port status; AOS LLDP via `alcatel_aos` |
| `mikrotik` | `mikrotik_routeros` | — | `/interface print brief` | `/interface print detail …` | Community; no LLDP path |
| `ericsson` | `ericsson_ipos` | stub | — | — | No community LLDP/iface templates |

`alcatel_aos` device_type uses community `show lldp remote-system` (mapped to platform `alcatel_aos`).

## Adding a template

1. Put the file under the vendor folder, e.g. `zte/zte_zxros_show_xxx.textfsm`
2. Name: `{platform}_{command_slug}.textfsm` (same convention as ntc-templates)
3. Register in root `index` with a **relative path**:
   `zte/zte_zxros_show_xxx.textfsm, .*, zte_zxros, sh[[ow]] ...`
4. Align Value names with community templates when possible
5. Add unit tests under `tests/`

When community already works, no file is required.
When community is wrong/incomplete for our lab, add an override under the vendor folder
with the same Platform+Command so NetX wins.

## Concurrency

Template files are read-only and safe across processes. Parsing goes through
`netx_api.ntc_parse` (fresh `CliTable` + process lock; never share instances across threads).
