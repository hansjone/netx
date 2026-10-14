# NetX Linux 发行版实施计划（2026-10-14）

> 修订（同日）：用户确认**只出 x86_64**；**API/Web 端口也必须可自选**（服务器上服务多，避免冲突）。下文已按此调整；由 Claude 直接实现。

## 0. 目标与已确认的需求

| # | 需求（用户确认） | 落地方式 |
|---|---|---|
| 1 | 覆盖尽量多的 Linux 发行版 | 以 **glibc ≥ 2.17** 为基线（CentOS 7 级别），只出 **x86_64** 包 |
| 2 | tar.gz + install.sh；离线安装，目标机上**什么都不用装** | 包内自带 Python 3.12、全部依赖、PostgreSQL 16 和 web/dist；目标机只需要 bash、tar、gzip、coreutils 和 useradd，这些是任何 glibc 发行版的最小系统都有的 |
| 3 | 在线更新 | `netxctl update`，更新源与 Windows 版一致（GitHub / Forgejo / manifest），可选每日自动更新（systemd timer）；失败自动回滚程序 |
| 4a | API/Web 端口可配置 | 安装时 `--port` 或交互输入（检查占用，被占用时重新输入）；装好后用 `netxctl set-port`，失败自动回滚 |
| 4b | 内置 PG 端口可配置 | Linux：安装时 `--pg-port` 或交互输入，装好后用 `netxctl set-pg-port`；**Windows 同步补齐**：Setup 数据库页加端口框，静默参数 `/BundledPgPort=`，另提供改端口脚本 |

现状：后端是纯 Python，依赖都有 manylinux2014 wheel，[host_metrics.py](../../netx_api/host_metrics.py) 已有 Linux 分支。所以 **`netx_api` 业务代码不需要改**，工作量都在 `packaging/linux/`、CI 和 Windows 的端口配置上。`scripts/start_netx.sh` / `stop_netx.sh` 保留，继续作为源码/开发模式的启动方式，不动。

### 支持范围

- **支持**（glibc ≥ 2.17，x86_64）：CentOS 7/8/Stream 9、RHEL 7–9、Rocky/Alma 8–9、Oracle Linux、Fedora、Ubuntu 18.04+、Debian 9+、openSUSE Leap/SLES 12+、openEuler 20.03+、麒麟 V10、统信 UOS 20、Anolis 等。
- **不支持（第一期）**：ARM（鲲鹏/飞腾）、Alpine 等 musl 系统、32 位系统、CentOS 6（glibc 2.12）、龙芯 LoongArch / MIPS（没有现成的 standalone Python，后续单独评估）。
- 有没有 systemd 都能用：有 systemd 就注册服务；没有（容器、精简系统）就用 `netxctl start/stop` 后台运行。

---

## 1. 产物与目录布局

### 1.1 发布产物（与 Windows Setup 挂在同一个 GitHub/Forgejo Release 下）

```
netx-<ver>-linux-x86_64.tar.gz        + .sha256
```

解压后：

```
netx-<ver>-linux-x86_64/
  install.sh                 # 入口：安装 / 更新 / 重装
  version.json               # {"version": "...", "arch": "x86_64", "pg_major": 16, "python": "3.12.x"}
  netx_api/ alembic/ alembic.ini pyproject.toml requirements.txt README.md LICENSE
  web/dist/                  # 由 API 托管，不需要 Node
  python/                    # python-build-standalone 3.12，依赖直接装在它的 site-packages 里（不用 venv）
  pgsql/{bin,lib,share}      # PostgreSQL 16 只保留运行需要的部分；rpath=$ORIGIN/../lib
  packaging/linux/           # netxctl、netx_admin.py、systemd 单元模板、logrotate、README
```

### 1.2 安装后的目录

| 路径 | 内容 | 属主 / 权限 |
|---|---|---|
| `/opt/netx/releases/<ver>/` | 某个版本的程序（只读） | root:root 0755 |
| `/opt/netx/current` → `releases/<ver>` | 当前版本的软链，切换是原子的 | |
| `/usr/bin/netxctl` → `/opt/netx/current/packaging/linux/netxctl` | 管理命令 | |
| `/var/lib/netx/` | `.env`、`pgdata/`、`data/…`（与 Windows 的 `Get-NetxDataEnvMap` 子目录一致）、`backups/`、`logs/` | netx:netx 0750，`.env` 0600 |
| `/etc/systemd/system/netx.service`、`netx-update.{service,timer}` | 服务单元 | |

`--prefix` 和 `--data` 都可以改。始终只保留最近 2 个 release 目录，用于回滚。

---

## 2. 任务清单

### L1 构建：PostgreSQL 16 可移植二进制（glibc 2.17）

- 新文件 `packaging/linux/build_pg.sh <arch>`：在 `quay.io/pypa/manylinux2014_<arch>` 容器里从源码编译 PostgreSQL 16.x，**和 Windows 用同一个小版本**（现在是 16.4，见 `packaging/cache/pgsql-16.4-1`）。
  - `./configure --prefix=/pgsql --without-readline --without-icu --with-zlib --without-openssl LDFLAGS='-Wl,-rpath,$ORIGIN/../lib'`
  - 把 `libz.so.1` 复制进 `pgsql/lib/`，避免最小系统里没有 zlib。
  - 只保留运行需要的二进制：`postgres pg_ctl initdb psql pg_dump pg_dumpall pg_restore pg_isready`（与 [build_release.ps1:163](../../packaging/build_release.ps1:163) 一致），外加 `lib/`、`share/`。
  - 打包成 `pg-16.x-linux-<arch>.tar.gz`；CI 按版本+架构缓存，本地放 `packaging/cache/`。
- 版本号和下载校验值统一写在 `packaging/linux/versions.env`（PG 源码 URL+sha256，python-build-standalone URL+sha256）。

**验收**：在 `centos:7` 容器里，`pgsql/bin/initdb` / `pg_ctl start` / `psql -c 'select version()'` 都能跑；`L4` 的检查脚本通过。

### L2 构建：Python 运行时 + 依赖

- `packaging/linux/build_python.sh <arch>`：下载 python-build-standalone `cpython-3.12.x+<date>-<arch>-unknown-linux-gnu-install_only_stripped.tar.gz`（pin 住版本并校验 sha256）。
- **在 manylinux2014 容器里**执行 `python/bin/python3 -m pip install --only-binary=:all: -r requirements.txt`。容器的 glibc 是 2.17，pip 只会选兼容的 wheel；任何依赖要编译或要求更高的 glibc，构建就会直接失败。
- 删掉 `test/`、`idlelib/`、`tkinter/`、`ensurepip/`，再执行 `compileall -q`，这样 `/opt` 只读也不会在运行时写 pyc。
- 不用 venv（standalone Python 本身可以搬动，venv 的软链和绝对路径反而碍事）。所有启动都用 `python/bin/python3 -m …`，不依赖 `bin/` 下脚本的 shebang。

**验收**：在 `centos:7` 和 `ubuntu:24.04` 里，`python/bin/python3 -c "import netx_api.main, psycopg, cryptography, netmiko, bcrypt, psutil"` 都成功。

### L3 构建：打包脚本 `packaging/linux/build_linux_release.sh`

- 参数：`--version`（默认读 pyproject）、`--web-dist <path>`、`--skip-web-build`。
- 流程：先检查 web/dist（没有就执行 `npm ci && npm run build:release`），调用 L1/L2，拷贝程序文件（清单与 [build_release.ps1:88-93](../../packaging/build_release.ps1:88) 一致），写 `version.json`，执行 `tar czf` 并生成 `.sha256`，最后调用 L4 检查。
- 依赖 docker（manylinux 容器）。本机是 Windows，所以本地构建要在 WSL2 里的 Ubuntu+docker 上做，或者用一台 Linux 构建机。**CI 是主要的构建途径**。
- 文件：`packaging/linux/README.md`，写清楚构建、安装、运维、支持范围。同时把 [packaging/README.md](../../packaging/README.md) 第 3 行"Linux is unchanged"改成指向 Linux 发行版的说明。

### L4 兼容性闸门 `packaging/linux/check_bundle.sh`

- 用 `objdump -T` 扫描包里所有 ELF（`python/`、`site-packages/**/*.so`、`pgsql/`），引用的 `GLIBC_x.y` 最高不能超过 **2.17**。
- 用 `readelf -d` 检查 NEEDED：除了 `libc/libm/libdl/libpthread/librt/libutil/libcrypt/ld-linux*/libgcc_s/libstdc++` 以外，都必须能在包内找到。
- 不通过就让构建失败。

### L5 `install.sh`（安装 / 更新 / 重装）

只依赖 bash、coreutils、tar、gzip、useradd（或 `adduser`）。凡是复杂逻辑（改 .env、检查端口、URL 编码、生成密钥）都交给**包里自带的 Python**（`packaging/linux/netx_admin.py`，只用标准库），不依赖 curl、ss、openssl 这些。

参数（交互模式下会逐项提问；中文还是英文按 `$LANG` 判断；`--yes` 为非交互）：

```
--mode install|update|reinstall   # 已有安装时默认 update
--prefix /opt/netx  --data /var/lib/netx
--db bundled|external
--pg-port 15432                   # 内置 PG 端口（需求 4）
--pg-password <pw>                # 不填则自动生成
--db-url postgresql+psycopg://…   # external 用；也可以拆成 --db-host/--db-port/--db-user/--db-password/--db-name
--bind 0.0.0.0  --port 8890       # Linux 一般是无桌面的服务器，默认绑 0.0.0.0（Windows 是 127.0.0.1）
--credential-key-file <path>      # 复用别的安装的 NE 密码加密密钥（对应 Windows 的 /CredentialSecretKey）
--no-service                      # 不注册 systemd 服务
--auto-update                     # 打开每日自动更新（默认关闭，与 Windows 一致）
--open-firewall                   # 有 firewall-cmd / ufw 时放行 --port；默认只打印提示
--no-backup                       # 更新前不做 pg_dump
```

预检（失败时给出中英文提示并退出，不留下半装的状态）：
1. 必须是 root；
2. 架构和包一致；`getconf GNU_LIBC_VERSION` ≥ 2.17；检测到 musl 就拒绝；
3. 磁盘空间够（≥ 1 GB）；
4. 用自带 Python 检查 `--port`、`--pg-port` 是否空闲（被占用时交互模式重新问，`--yes` 模式直接报 `port_in_use`）；
5. external 模式用 `psql SELECT 1` 测试连接（对应 Windows 的行为）。

**首次安装**的步骤：
1. 创建系统用户 `netx`（`useradd -r -s /sbin/nologin -d /var/lib/netx`；没有 useradd 就退到 `adduser -S`）；
2. 解压到 `releases/<ver>`，切换 `current` 软链，建立 `netxctl` 链接；
3. 创建数据目录、写 `.env`（键与 Windows [setup_first_run.ps1](../../packaging/setup_first_run.ps1) 一致：`NETX_DB_MODE`、`NETX_HOST`、`NETX_PORT`、`NETX_UI_DIST_DIR`、`NETX_BUNDLED_PG_PORT`、`NETX_DATABASE_URL`、`Get-NetxDataEnvMap` 里的路径、凭据密钥）；
4. 内置 PG：用 `netx` 用户执行 `initdb -U postgres -A scram-sha-256 -E UTF8 --locale=C`，在 `postgresql.conf` 写入 `port = <pg-port>` 和 `listen_addresses = '127.0.0.1'`，`pg_hba` 只允许本机，然后创建 `netx` 角色和库（照搬 Windows 的 bootstrap SQL）；
5. 如果系统有 SELinux 并且有 `restorecon`，执行 `restorecon -R <prefix>`；
6. 有 systemd（`[ -d /run/systemd/system ]`）就安装 `netx.service` 并 `enable --now`；没有就执行 `netxctl start`；
7. 等待 `/health` 返回正常（最多 90 秒），最后打印访问地址、首次登录说明和防火墙提示。

**更新**（`--mode update`，也是在线更新最后调用的入口）：
1. 解压到 `releases/<newver>`（版本已存在就覆盖）；
2. 内置 PG 时做 `pg_dump -Fc` 到 `backups/pre-<newver>-<ts>.dump`，只保留最近 3 份；
3. 停服务，切换 `current` 软链，启动，等待 `/health`；
4. 如果没有起来：软链切回旧版本再启动，返回非 0，并提示备份文件的位置（数据库可能已经被 alembic 迁移过，所以只回滚程序，数据库靠这个 dump 恢复）；
5. 成功后清理旧 release，只保留 2 个；
6. 不会改动 `.env` 和 PG 端口；systemd 单元按新版本的模板重新生成。

`--mode reinstall`：重新走数据库配置，但**不删除** `/var/lib/netx`（和 Windows 一样）。

### L6 `netxctl` 管理命令

bash 写的薄入口，复杂的子命令转交 `netx_admin.py`。以 root 调用时，需要降权的操作用 `runuser -u netx --`（没有 runuser 就用 `su -s /bin/sh netx -c`）。

```
netxctl run                 # 前台监督进程（systemd 的 ExecStart 就是它）
netxctl start|stop|restart|status
netxctl logs [-f] [api|worker|pg]
netxctl set-pg-port <port>  # 需求 4
netxctl set-port <port> / set-bind <host>
netxctl reconfigure-db      # 等于 install.sh --mode reinstall 里的数据库部分
netxctl check-update / update [--yes] [--file <tar.gz>]
netxctl auto-update on|off
netxctl backup [<file>]     # pg_dump + data/auth 下的密钥，打成一个 tar.gz
netxctl uninstall [--purge] # --purge 删除数据，需要输入 "DELETE" 确认
netxctl version
```

`netxctl run`（前台监督进程，对应 Windows 的 [service_run.ps1](../../packaging/service_run.ps1)）：
- 加载 `/var/lib/netx/.env`，并强制覆盖为数据目录下的路径；设置 `PYTHONPATH=current`、`NETX_UI_DIST_DIR`；
- 内置模式先执行 `pg_ctl -D pgdata -w start`。启动前检查 `pgdata/PG_VERSION` 的主版本与 `version.json` 里的 `pg_major` 是否一致，不一致就报 `pg_major_mismatch`，不擅自升级数据；
- 启动 API（`-m netx_api.main`）、worker（`-m netx_api.worker`）和 biz_state workers，副本数和参数照搬 [start_netx.sh:122-160](../../scripts/start_netx.sh:122)，`NETX_RUN_INLINE_SCHEDULERS=false`；
- `trap TERM INT`：先停所有子进程，再 `pg_ctl stop -m fast`；
- 用 `wait -n` 等待：任何一个子进程退出就整体退出并返回非 0，由 systemd `Restart=on-failure` 重启；
- 日志写到 `/var/lib/netx/logs/*.log`，并安装 `/etc/logrotate.d/netx`（copytruncate，7 份）。

没有 systemd 时：`netxctl start` 执行 `nohup netxctl run &`，pid 写到 `data/runtime/netx.pid`；`stop` 发 TERM，30 秒后还没退出就 KILL。

`netx.service`：

```ini
[Unit]
Description=NetX
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=netx
Group=netx
ExecStart=/opt/netx/current/packaging/linux/netxctl run
KillMode=mixed
TimeoutStopSec=60
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
[Install]
WantedBy=multi-user.target
```

### L7a API/Web 端口可配置

- `install.sh`：支持 `--port` 和 `--bind`；交互模式下默认 8890 / 0.0.0.0，会检查端口是否被占用（自带 Python 的 socket 检测），被占用时重新问，`--yes` 模式直接报 `port_in_use`。
- `netxctl set-port <port>` / `set-bind <host>`：校验 → 停服务 → 改 `.env` 里的 `NETX_PORT` / `NETX_HOST` → 启动 → 等 `/health`；失败就恢复 `.env` 并重新启动。改完后打印 firewalld/ufw 的放行命令。
- 重装（`--mode reinstall`）时，默认值取当前配置，并允许沿用自己正在占用的端口。

### L7 内置 PG 端口可配置（Linux 部分）

- `install.sh`：支持 `--pg-port`；交互模式默认值 15432，会检查是否被占用，被占用时重新问。
- `netxctl set-pg-port <port>`：校验范围在 1024–65535 且端口空闲 → 停服务 → 改 `postgresql.conf` 里的 `port` → 改 `.env` 里的 `NETX_BUNDLED_PG_PORT`，并把 `NETX_DATABASE_URL` 中的端口一并改掉（只换 host:port 部分，密码保持原样）→ 启动 → 等 `/health`；失败就把三处改回去。
- external 模式下执行这个命令会报错，提示改用 `reconfigure-db`。

### L8 在线更新 `packaging/linux/netx_update.py`（只用标准库）

- 更新源和配置项与 Windows [check_update.ps1](../../packaging/check_update.ps1) **完全一致**：`NETX_UPDATE_SOURCES`（默认 `github,forgejo`）、`NETX_UPDATE_GITHUB_REPO`、`NETX_UPDATE_FORGEJO_URL`、`NETX_UPDATE_URL`（manifest）、`NETX_UPDATE_TOKEN`；会探测所有源，选最新的那个；版本相同时优先 GitHub；token 只发给非 GitHub 的主机。
- 资产匹配规则：`^netx-(?P<ver>[\d.]+)-linux-<arch>\.tar\.gz$`；sha256 优先取 `<asset>.sha256` 资产，其次取 manifest 里的值，**两者都没有就拒绝更新**。
- 代理：遵循 `HTTPS_PROXY/NO_PROXY`（urllib 默认就支持）。
- 流程：下载到 `/var/lib/netx/updates/` → 校验 → 解压到临时目录 → 执行**新包里的** `install.sh --mode update --yes` → 清理临时文件。
- `netxctl update --file <tar.gz>`：离线升级，跳过下载这一步。
- 自动更新：`netx-update.timer`（`OnCalendar=*-*-* 03:30`，`RandomizedDelaySec=1h`，`Persistent=true`），执行 `netxctl update --yes --quiet`。没有 systemd 但有 crontab 时写到 `/etc/cron.d/netx-update`；两者都没有就提示不支持。
- [manifest.example.json](../../packaging/manifest.example.json) 增加：
  ```json
  "linux": {
    "x86_64": {"url": "...netx-0.4.19-linux-x86_64.tar.gz",  "sha256": "", "size": 0},
  }
  ```
- Windows 这边 [check_update.ps1:115](../../packaging/check_update.ps1:115) 只匹配 `Setup-.*\.exe`，同一个 Release 里出现 Linux 资产不受影响。需要加一条回归验证。

### L9 CI：`.github/workflows/release-linux.yml`

- 触发：`v*` tag（和 Windows 一致）；同样校验 tag 必须等于 pyproject 里的版本。
- job `web`：Node 20，执行 `npm ci && npm run build:release`，上传 `web/dist` 作为 artifact。
- job `build`：`ubuntu-latest` + manylinux2014_x86_64 容器
  - 步骤：缓存/构建 PG（L1），执行 L2、L3、L4，上传 tar.gz 和 sha256 artifact。
- job `smoke`（必须通过才发布），x86_64 在下面这些镜像里各跑一遍（`docker run --network none`，同时验证离线安装）：
  `centos:7`、`rockylinux:9`、`ubuntu:18.04`、`ubuntu:24.04`、`debian:12`、`opensuse/leap:15`、`openeuler/openeuler:22.03`。
  每个镜像执行 `packaging/linux/smoke_test.sh`：
  1. `install.sh --yes --no-service --db bundled --pg-port 25432`
  2. `/health` 返回 200；用默认管理员登录拿到 token
  3. `netxctl set-pg-port 25433`，之后 `/health` 仍然是 200
  4. `install.sh --mode update --yes`（用同一个包模拟升级）：`backups/` 里多出一份 dump，数据还在（登录前先建一条测试数据）
  5. `netxctl stop`，检查没有残留进程
  6. `netxctl uninstall --purge`（用 `--yes` 跳过确认）
- job `release`：用 `softprops/action-gh-release@v2` 把 4 个文件上传到同一个 tag 的 Release（**不要**设 `generate_release_notes`，release notes 由 Windows 的 workflow 生成，避免互相覆盖）。

### L10 发布脚本

- [publish_forgejo_release.ps1](../../packaging/publish_forgejo_release.ps1)：`packaging/release/` 下如果有 `netx-<ver>-linux-*.tar.gz(.sha256)`，就一起上传；Release 说明里加上 Linux 安装方式。
- [publish_release.ps1](../../packaging/publish_release.ps1)：GitHub Release 说明模板（第 107 行附近）加一段 Linux 的说明。
- 本地发布流程（写进 `packaging/linux/README.md`）：CI 发布 tag 后，用 `gh release download vX -p 'netx-*-linux-*'` 把 Linux 包取回 `packaging/release/`，再执行 `publish_forgejo_release.ps1` 同步到 Forgejo。

### W1 Windows：Setup 数据库页增加内置 PG 端口

- [netx.iss](../../packaging/installer/netx.iss)：在 `RbBundled`（约第 762 行）下面加一个"端口"输入框 `EdBundledPort`，默认 15432，只有选内置时才可用（`DbModeClick`，第 469 行）。翻页时校验它是 1024–65535 的数字，并且端口没有被占用（可以复用 `Test-NetxTcpPortFree`，或者在 Pascal 里用 `netstat -ano` 检查）。
- 静默安装参数 `/BundledPgPort=25432`（加在第 644 行附近解析 `DbMode` 的地方）。
- 执行 `setup_first_run.ps1` 时传入 `-BundledPort`（这个参数已经存在，见 [setup_first_run.ps1:11](../../packaging/setup_first_run.ps1:11)）。
- **Update 模式**不显示、也不修改端口。
- 中英文文案（`english.` / `chinesesimplified.`）。

### W2 Windows：安装后修改端口

- 新文件 `packaging/set_pg_port.ps1 -Port <n>`（会自动提权）：做的事和 L7 一样：校验 → 通过 `runtime_ctl.ps1` 停服务 → 改 `postgresql.conf`、`.env` 里的 `NETX_BUNDLED_PG_PORT` 和 `NETX_DATABASE_URL` 的端口 → 启动 → 等 `/health`，失败就回滚。
- 托盘的"设置"里加一项"内置数据库端口…"，调用这个脚本。
- [build_release.ps1:121](../../packaging/build_release.ps1:121) 的拷贝清单里加上 `set_pg_port.ps1`。

---

## 3. 验收清单

| ID | 场景 | 通过标准 |
|---|---|---|
| A1 | CI 构建 | `release-linux.yml` 在测试 tag 上完整跑通；两个架构的 tar.gz 都 ≤ 150 MB；L4 检查通过 |
| A2 | 离线安装矩阵 | L9 的 smoke 在全部镜像里通过，并且都是 `--network none` |
| A3 | 真机 + systemd | 在一台真实的 Linux 服务器（例如 10.0.0.131 或一台 CentOS 7 / 麒麟虚拟机）上交互式安装：开机自启；`systemctl restart netx` 正常；`kill -9` API 进程后 5 秒内被拉起；浏览器能打开 `http://<ip>:8890/` 并登录 |
| A4 | PG 端口 | 安装时 `--pg-port 25432` 生效（`ss -lnt` 只看到 `127.0.0.1:25432`）；`netxctl set-pg-port 25433` 后服务正常；改成一个已被占用的端口会被拒绝，原配置不变 |
| A4b | API 端口 | 安装时 `--port 18890` 生效；`netxctl set-port 18891` 后新端口能访问、旧端口不再监听；设成一个已被占用的端口会被拒绝 |
| A5 | 在线更新 | 先装 vN，在 Release 上挂 vN+1，执行 `netxctl check-update` 能看到新版本；`netxctl update --yes` 成功，`current` 指向新版本，数据还在，`backups/` 里有 dump |
| A6 | 更新回滚 | 构造一个启动就失败的包（比如删掉 `netx_api/main.py`），`netxctl update --file` 之后 `current` 自动切回旧版本，服务正常，命令返回非 0 |
| A7 | 校验失败 | 篡改 `.sha256` 后执行 update，结果是拒绝更新，并且不会解压 |
| A8 | 自动更新 | `netxctl auto-update on` 后 `systemctl list-timers` 里能看到 `netx-update.timer`；`off` 后消失 |
| A9 | external 模式 | `--db external --db-url …` 连接到外部 PG 能正常工作；`set-pg-port` 会给出明确的报错 |
| A10 | 卸载 | `netxctl uninstall` 保留 `/var/lib/netx`；`--purge` 加确认后全部清掉；systemd 单元和 `netxctl` 链接都被删除 |
| A11 | Windows 端口 | Setup 选内置并把端口填成 25432：装好后 `.env` 和 `postgresql.conf` 一致，服务正常；静默 `/BundledPgPort=25432` 一样生效；Update 模式下端口不变；托盘改端口成功 |
| A12 | Windows 回归 | 现有的 Windows 沙箱离线验证（静默安装、启动、登录、停止、服务模式）全部通过；同一个 Release 里有 Linux 资产时，Windows 的 `check_update.ps1` 仍然只选 Setup.exe |

## 4. 建议的实现顺序

1. L1 → L2 → L4（先把"能在 CentOS 7 上运行的运行时"做出来，这是风险最大的部分）
2. L6 `netxctl run`/start/stop → L5 install.sh → L7
3. L3 + L9（CI + smoke 矩阵）
4. L8 在线更新 + L10 发布脚本
5. W1、W2（Windows 端口，相对独立，可以并行做）

## 5. 风险与说明

- **PG 大版本**：固定为 16。以后要升到 17 需要单独做 `pg_upgrade` 流程；第一期只检测、不处理（`pg_major_mismatch`）。
- **API 默认绑 0.0.0.0**：Linux 服务器一般通过远程访问，所以默认对外。安装完成后会醒目提示修改默认管理员密码，并提醒放通防火墙端口。
- **本地构建**：本机目前没有 docker，WSL 里也没有装发行版。要在本地构建 Linux 包，需要 `wsl --install -d Ubuntu` 再装 docker，或者在一台 Linux 构建机上执行。日常发布以 CI 为主。
