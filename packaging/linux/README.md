# NetX Linux 发行版 / Linux release

一个离线安装包 `netx-<版本>-linux-x86_64.tar.gz`：自带 Python 3.12、全部依赖、PostgreSQL 16 和网页，目标机器上**不需要预装任何软件**（只要 bash、tar、gzip、useradd 这些系统自带命令）。

## 支持的系统

x86_64、glibc ≥ 2.17：CentOS 7/8/Stream 9、RHEL 7–9、Rocky/Alma 8–9、Oracle Linux、Fedora、Ubuntu 18.04+、Debian 9+、openSUSE Leap / SLES 12+、openEuler 20.03+、麒麟 V10、统信 UOS 20、Anolis 等。

不支持：Alpine（musl）、32 位、ARM、CentOS 6。

## 安装

```bash
tar xzf netx-0.4.19-linux-x86_64.tar.gz
cd netx-0.4.19-linux-x86_64
sudo ./install.sh
```

交互式安装会依次询问：监听地址（默认 `0.0.0.0`）、**Web/API 端口**（默认 8890，被占用会提示重新输入）、数据库（内置 / 外置）、**内置 PostgreSQL 端口**（默认 15432，只监听 127.0.0.1）。

非交互示例：

```bash
sudo ./install.sh --yes --port 9890 --db bundled --pg-port 25432
sudo ./install.sh --yes --db external --db-url 'postgresql+psycopg://netx:密码@10.0.0.8:5432/netx'
```

装好后访问 `http://<服务器IP>:<端口>/`，初始账号 `admin`，密码在安装结束时显示（随机生成，也可用 `--admin-password` 指定；同时保存在 `/var/lib/netx/.env` 的 `NETX_BOOTSTRAP_ADMIN_PASSWORD`），首次登录必须改密码。

> 对外监听（非 127.0.0.1）时 NetX 自身要求：管理员初始密码不能是 admin123，并且开启 UME 的 TLS 校验。安装程序会自动设置 `NETX_UME_VERIFY_TLS=true`；如果 UME 用的是自签名证书，需要配置证书，或者改成只监听本机再通过反向代理访问。

| 路径 | 内容 |
|---|---|
| `/opt/netx/current` → `releases/<版本>` | 程序（只读，保留最近 2 个版本用于回滚） |
| `/var/lib/netx` | `.env`、`pgdata/`、`data/`、`logs/`、`backups/` |
| `/usr/bin/netxctl` | 管理命令 |
| `netx.service` | systemd 服务，开机自启；没有 systemd 时用 `netxctl start` |

服务以系统用户 `netx` 运行。

## 日常管理

```bash
sudo netxctl status
sudo netxctl stop | start | restart
sudo netxctl logs api -f            # api / worker / biz0 / pg / netxctl
sudo netxctl set-port 9890     # 改 Web/API 端口（检查占用，失败自动回滚）
sudo netxctl set-bind 127.0.0.1
sudo netxctl set-pg-port 25432 # 改内置 PostgreSQL 端口
sudo netxctl reconfigure-db    # 重新选择内置/外置数据库（不删数据）
sudo netxctl backup            # pg_dump + .env + 密钥 → /var/lib/netx/backups/
sudo netxctl uninstall         # 保留数据；--purge 连数据一起删除
```

## 更新

- 在线：`sudo netxctl check-update`、`sudo netxctl update`。更新源与 Windows 版一致（GitHub、Forgejo，可用 `.env` 里的 `NETX_UPDATE_*` 覆盖），下载后校验 sha256。
- 离线：`sudo netxctl update --file netx-0.4.20-linux-x86_64.tar.gz`，或者解压新包后执行 `sudo ./install.sh`（自动识别为更新）。
- 自动：`sudo netxctl auto-update on`（每天 03:30 左右；systemd timer，没有 systemd 时用 cron）。

更新会先停服务，把内置数据库备份到 `backups/pre-<版本>-*.dump`（保留 3 份），然后切换版本并启动。新版本没起来就自动切回旧版本。端口、数据库等配置保持不变。

## 构建（开发者）

需要 docker，构建在 `manylinux2014` 容器里进行（CentOS 7 / glibc 2.17），保证产物在老系统上也能运行：

```bash
packaging/linux/build_linux_release.sh            # 产物在 packaging/release/
packaging/linux/build_linux_release.sh --skip-web-build   # 复用现成的 web/dist
```

- 第一次会从源码编译 PostgreSQL，结果缓存在 `packaging/cache/linux/`。
- `check_bundle.sh` 检查所有 ELF：要求的 glibc 不超过 2.17，并且依赖的库都在包里。
- 冒烟测试（断网，在一个裸发行版容器里完整走一遍安装、改端口、更新和卸载）：

```bash
docker run --rm --network none -v "$PWD/packaging/release:/pkg:ro" \
  -v "$PWD/packaging/linux/smoke_test.sh:/smoke.sh:ro" centos:7 \
  bash /smoke.sh /pkg/netx-0.4.19-linux-x86_64.tar.gz
```

CI：`.github/workflows/release-linux.yml` 在 `v*` tag 上构建，在 7 个发行版里跑冒烟测试，全部通过后把 tar.gz 和 .sha256 挂到同一个 Release。挂到 Forgejo 用 `packaging/publish_forgejo_release.ps1`，它会自动带上 `packaging/release/` 下的 Linux 包。
