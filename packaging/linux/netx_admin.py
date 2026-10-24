"""NetX Linux admin helper (stdlib only, run with the bundled Python).

Used by install.sh and netxctl for the bits that are painful in portable bash:
.env editing, port probes, URL building, health waits and update downloads.

Every subcommand prints machine-readable output on stdout and exits non-zero on
failure with a short `error_code: detail` line on stderr.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_GITHUB_REPO = "hansjone/netx"
DEFAULT_FORGEJO_URL = "https://git.hansjone.top/api/v1/repos/hansjone/netx/releases/latest"
USER_AGENT = "netx-linux-updater"


def die(code: str, detail: str = "", rc: int = 1) -> None:
    sys.stderr.write(f"{code}: {detail}\n" if detail else f"{code}\n")
    sys.exit(rc)


# --------------------------------------------------------------------------- .env


def _parse_env_line(line: str) -> tuple[str, str] | None:
    t = line.strip()
    if not t or t.startswith("#") or "=" not in t:
        return None
    k, v = t.split("=", 1)
    k = k.strip()
    if k.startswith("export "):
        k = k[len("export ") :].strip()
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return k, v


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        kv = _parse_env_line(line)
        if kv:
            out[kv[0]] = kv[1]
    return out


def write_env(path: Path, values: dict[str, str], unset: list[str] | None = None) -> None:
    """Update keys in place (keeping comments/order), append new ones."""
    unset_set = set(unset or [])
    lines: list[str] = []
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            kv = _parse_env_line(line)
            if kv and kv[0] in unset_set:
                continue
            if kv and kv[0] in values:
                if kv[0] in seen:
                    continue
                lines.append(f"{kv[0]}={values[kv[0]]}")
                seen.add(kv[0])
                continue
            lines.append(line)
    for k, v in values.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        st = path.stat()
        if hasattr(os, "chown"):
            os.chown(tmp, st.st_uid, st.st_gid)
    except (FileNotFoundError, PermissionError):
        pass
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- net


_BIND_ALIASES = {"": "0.0.0.0", "all": "0.0.0.0", "any": "0.0.0.0", "*": "0.0.0.0",
                 "local": "127.0.0.1", "localhost": "127.0.0.1", "loopback": "127.0.0.1"}


def norm_bind(value: str) -> str:
    """Listen address as NetX needs it: an IPv4 literal present on this host.

    Accepts aliases (all/*, local/localhost). Raises ValueError with a reason.
    """
    v = _BIND_ALIASES.get(value.strip().lower(), value.strip())
    try:
        socket.inet_aton(v)
        if v.count(".") != 3:
            raise OSError
    except OSError:
        raise ValueError(f"not an IPv4 address: {value!r} (use 0.0.0.0 for all interfaces)")
    if v != "0.0.0.0":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((v, 0))
        except OSError:
            raise ValueError(f"{v} is not an address of this server")
        finally:
            s.close()
    return v


def port_free(port: int, host: str = "0.0.0.0") -> bool:
    """True when nothing listens on `port` (checks the wildcard and loopback).

    Only EADDRINUSE/EACCES mean "taken"; any other bind error is a bad address
    and must not be reported as a busy port.
    """
    import errno

    for h in {host, "0.0.0.0", "127.0.0.1"}:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((h, port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                return False
            raise ValueError(f"cannot bind {h}:{port}: {exc}")
        finally:
            s.close()
    # Also catch a listener bound only to a specific address we did not test.
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return False
    except OSError:
        pass
    return True


def valid_port(p: str) -> int:
    try:
        n = int(p)
    except ValueError:
        die("invalid_port", p)
    if not 1024 <= n <= 65535:
        die("invalid_port", f"{p} (must be 1024-65535)")
    return n


def http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# --------------------------------------------------------------------------- db url

_URL_RE = re.compile(
    r"^(?P<scheme>postgres(?:ql)?(?:\+psycopg)?)://(?P<user>[^:@/]+)(?::(?P<pw>[^@]*))?@(?P<host>\[[^\]]+\]|[^:/?]+)(?::(?P<port>\d+))?/(?P<db>[^?\s]+)(?P<rest>\?.*)?$"
)


def build_url(user: str, pw: str, host: str, port: int, db: str) -> str:
    q = urllib.parse.quote
    return f"postgresql+psycopg://{q(user, safe='')}:{q(pw, safe='')}@{host}:{port}/{q(db, safe='')}"


def url_with_port(url: str, port: int) -> str:
    m = _URL_RE.match(url.strip())
    if not m:
        die("db_url_unparseable")
    pw = f":{m['pw']}" if m["pw"] is not None else ""
    return f"{m['scheme']}://{m['user']}{pw}@{m['host']}:{port}/{m['db']}{m['rest'] or ''}"


def url_parts(url: str) -> dict[str, str]:
    m = _URL_RE.match(url.strip())
    if not m:
        die("db_url_unparseable")
    u = urllib.parse.unquote
    return {
        "user": u(m["user"]),
        "password": u(m["pw"] or ""),
        "host": m["host"].strip("[]"),
        "port": m["port"] or "5432",
        "db": u(m["db"]),
    }


def probe_db(url: str) -> None:
    """Connect with the bundled psycopg and run SELECT 1."""
    p = url_parts(url)
    try:
        import psycopg  # type: ignore
    except Exception as exc:  # pragma: no cover - only without bundled deps
        die("psycopg_unavailable", str(exc))
    try:
        with psycopg.connect(
            host=p["host"],
            port=int(p["port"]),
            user=p["user"],
            password=p["password"],
            dbname=p["db"],
            connect_timeout=8,
        ) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        die("external_db_probe_failed", str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc))


# --------------------------------------------------------------------------- pg conf


def pg_conf_set(conf: Path, settings: dict[str, str]) -> None:
    text = conf.read_text(encoding="utf-8", errors="replace") if conf.exists() else ""
    for key, val in settings.items():
        pat = re.compile(rf"(?m)^\s*#?\s*{re.escape(key)}\s*=.*$")
        line = f"{key} = {val}"
        # Replace the first (possibly commented) occurrence, drop later active duplicates.
        found = False

        def _sub(m: re.Match[str]) -> str:
            nonlocal found
            if not found:
                found = True
                return line
            return "" if not m.group(0).lstrip().startswith("#") else m.group(0)

        text = pat.sub(_sub, text)
        if not found:
            text = text.rstrip("\n") + f"\n{line}\n"
    conf.write_text(text, encoding="utf-8")


def pg_conf_get_port(conf: Path) -> str:
    for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*port\s*=\s*(\d+)", line)
        if m:
            return m.group(1)
    return "5432"


# --------------------------------------------------------------------------- versions


def ver_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v.lstrip("vV"))
    return tuple(int(x) for x in parts[:4]) or (0,)


def current_version(prefix: Path) -> str:
    vj = prefix / "current" / "version.json"
    try:
        return str(json.loads(vj.read_text(encoding="utf-8")).get("version") or "0.0.0")
    except Exception:
        return "0.0.0"


# --------------------------------------------------------------------------- update


class Cfg:
    def __init__(self, env: dict[str, str]):
        def g(k: str, default: str = "") -> str:
            return (env.get(k) or os.environ.get(k) or default).strip()

        self.update_url = g("NETX_UPDATE_URL")
        self.fallback_url = g("NETX_UPDATE_FALLBACK_URL")
        self.forgejo_url = g("NETX_UPDATE_FORGEJO_URL", DEFAULT_FORGEJO_URL)
        self.github_repo = g("NETX_UPDATE_GITHUB_REPO", DEFAULT_GITHUB_REPO)
        self.sources = g("NETX_UPDATE_SOURCES")
        self.token = g("NETX_UPDATE_TOKEN")

    def source_list(self) -> list[str]:
        if self.sources:
            return [s.strip().lower() for s in self.sources.split(",") if s.strip()]
        out = []
        if self.update_url:
            out.append("manifest")
        out += ["github", "forgejo"]
        if self.fallback_url:
            out.append("manifest_fallback")
        return out


def _is_github(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == "github.com" or host.endswith(".github.com") or host.endswith("githubusercontent.com")


def _open(url: str, cfg: Cfg, accept: str = "application/json", style: str = "", timeout: int = 30):
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if cfg.token:
        if style == "bearer":
            headers["Authorization"] = f"Bearer {cfg.token}"
        elif style == "token" or (style == "" and not _is_github(url)):
            headers["Authorization"] = f"token {cfg.token}"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())


def _get_json(url: str, cfg: Cfg, style: str) -> dict:
    with _open(url, cfg, style=style) as r:
        return json.loads(r.read().decode("utf-8"))


def asset_name_re(arch: str) -> re.Pattern[str]:
    return re.compile(rf"^netx-(?P<ver>[0-9][0-9A-Za-z.\-]*)-linux-{re.escape(arch)}\.tar\.gz$")


def _release_to_candidate(rel: dict, source: str, arch: str, api_url: str = "") -> dict:
    tag = str(rel.get("tag_name") or "")
    assets = rel.get("assets") or []
    rx = asset_name_re(arch)
    pkg = next((a for a in assets if rx.match(str(a.get("name") or ""))), None)
    if not pkg:
        raise RuntimeError(f"{source}_release_missing_linux_{arch}")
    sha = next((a for a in assets if str(a.get("name") or "") == f"{pkg['name']}.sha256"), None)

    def url_of(a: dict) -> str:
        u = str(a.get("browser_download_url") or "")
        if u:
            return u
        m = re.match(r"^(https?://[^/]+)/api/v1/repos/([^/]+)/([^/]+)/releases/latest", api_url)
        if source == "forgejo" and m and tag:
            return f"{m[1]}/{m[2]}/{m[3]}/releases/download/{tag}/{a['name']}"
        return ""

    return {
        "source": source,
        "latest": tag.lstrip("vV"),
        # Forgejo serves /attachments/<uuid>: the URL does not carry the file name.
        "name": str(pkg["name"]),
        "url": url_of(pkg),
        "sha256_url": url_of(sha) if sha else "",
        "sha256": "",
        "size": int(pkg.get("size") or 0),
        "notes_url": str(rel.get("html_url") or ""),
    }


def _manifest_to_candidate(m: dict, source: str, arch: str) -> dict:
    linux = (m.get("linux") or {}).get(arch) or {}
    if not linux.get("url"):
        raise RuntimeError(f"{source}_manifest_missing_linux_{arch}")
    return {
        "source": m.get("source") or source,
        "latest": str(m.get("latest") or "").lstrip("vV"),
        "url": linux["url"],
        "sha256_url": linux.get("sha256_url") or "",
        "sha256": (linux.get("sha256") or "").lower(),
        "size": int(linux.get("size") or 0),
        "notes_url": m.get("notes_url") or "",
    }


def find_update(cfg: Cfg, arch: str, quiet: bool) -> tuple[dict | None, list[str]]:
    cands: list[dict] = []
    errors: list[str] = []
    for src in cfg.source_list():
        try:
            if src == "github":
                rel = _get_json(f"https://api.github.com/repos/{cfg.github_repo}/releases/latest", cfg, "bearer")
                c = _release_to_candidate(rel, "github", arch)
            elif src == "forgejo":
                rel = _get_json(cfg.forgejo_url, cfg, "token")
                c = _release_to_candidate(rel, "forgejo", arch, cfg.forgejo_url)
            elif src in ("manifest", "manifest_fallback"):
                url = cfg.update_url if src == "manifest" else cfg.fallback_url
                if not url:
                    raise RuntimeError(f"{src}_url_empty")
                c = _manifest_to_candidate(_get_json(url, cfg, "bearer"), src, arch)
            else:
                raise RuntimeError(f"unknown_source:{src}")
            if not c["url"]:
                raise RuntimeError(f"{src}_download_url_missing")
            cands.append(c)
            if not quiet:
                print(f"    OK {src} latest={c['latest']}", file=sys.stderr)
        except Exception as exc:
            errors.append(f"{src}: {exc}")
            if not quiet:
                print(f"[WARN] source '{src}' failed: {exc}", file=sys.stderr)
    best = None
    for c in cands:  # highest version wins; ties keep the earlier source
        if best is None or ver_tuple(c["latest"]) > ver_tuple(best["latest"]):
            best = c
    return best, errors


def download(url: str, dest: Path, cfg: Cfg) -> None:
    style = "none" if _is_github(url) else ""
    with _open(url, cfg, accept="application/octet-stream", style=style, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(tar_path: Path, dest: Path) -> Path:
    """Extract a release tarball, refusing absolute/parent paths and links outside."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        tops = set()
        for m in members:
            target = (root / m.name).resolve()
            if not str(target).startswith(str(root) + os.sep) and target != root:
                die("unsafe_tar_member", m.name)
            if m.issym() or m.islnk():
                link = (target.parent / m.linkname).resolve() if m.issym() else (root / m.linkname).resolve()
                if not str(link).startswith(str(root) + os.sep):
                    die("unsafe_tar_link", f"{m.name} -> {m.linkname}")
            if m.isdev():
                die("unsafe_tar_device", m.name)
            tops.add(m.name.split("/", 1)[0])
        if len(tops) != 1:
            die("bad_package_layout", ",".join(sorted(tops))[:200])
        try:
            tf.extractall(root, filter="tar")  # type: ignore[call-arg]
        except TypeError:  # Python without extraction filters
            tf.extractall(root)
    pkg = root / tops.pop()
    if not (pkg / "install.sh").is_file() or not (pkg / "version.json").is_file():
        die("bad_package_layout", "install.sh/version.json missing")
    return pkg


# --------------------------------------------------------------------------- commands


def cmd_env_get(a: argparse.Namespace) -> None:
    v = read_env(Path(a.file)).get(a.key)
    if v is None:
        sys.exit(1)
    print(v)


def cmd_env_set(a: argparse.Namespace) -> None:
    vals: dict[str, str] = {}
    for kv in a.pairs:
        if "=" not in kv:
            die("bad_pair", kv)
        k, v = kv.split("=", 1)
        vals[k] = v
    write_env(Path(a.file), vals, a.unset or [])


def cmd_env_export(a: argparse.Namespace) -> None:
    """NUL-separated KEY=VALUE pairs for `while read -d ''` in bash (no eval)."""
    for k, v in read_env(Path(a.file)).items():
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            sys.stdout.write(f"{k}={v}\0")


def cmd_port_free(a: argparse.Namespace) -> None:
    """exit 0 = free, 1 = in use, 3 = bad listen address."""
    try:
        free = port_free(valid_port(a.port), a.host)
    except ValueError as exc:
        die("bad_listen_address", str(exc), rc=3)
    sys.exit(0 if free else 1)


def cmd_norm_bind(a: argparse.Namespace) -> None:
    try:
        print(norm_bind(a.value))
    except ValueError as exc:
        die("bad_listen_address", str(exc))


def cmd_valid_port(a: argparse.Namespace) -> None:
    print(valid_port(a.port))


def cmd_gen_secret(a: argparse.Namespace) -> None:
    if a.kind == "fernet":
        print(base64.urlsafe_b64encode(os.urandom(32)).decode())
    else:
        # Alphanumeric only: safe in URLs, SQL literals and as a CLI argument (no leading "-").
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        print("".join(secrets.choice(alphabet) for _ in range(32)))


def cmd_build_url(a: argparse.Namespace) -> None:
    print(build_url(a.user, a.password, a.host, valid_port(a.port), a.db))


def cmd_url_port(a: argparse.Namespace) -> None:
    print(url_with_port(a.url, valid_port(a.port)))


def cmd_url_part(a: argparse.Namespace) -> None:
    print(url_parts(a.url)[a.part])


def cmd_probe_db(a: argparse.Namespace) -> None:
    probe_db(a.url)


def cmd_pg_conf(a: argparse.Namespace) -> None:
    conf = Path(a.conf)
    if a.get_port:
        print(pg_conf_get_port(conf))
        return
    vals = {}
    for kv in a.set or []:
        k, v = kv.split("=", 1)
        vals[k] = v
    pg_conf_set(conf, vals)


def cmd_wait_health(a: argparse.Namespace) -> None:
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        if http_ok(a.url):
            return
        time.sleep(1.5)
    sys.exit(1)


def cmd_health(a: argparse.Namespace) -> None:
    sys.exit(0 if http_ok(a.url) else 1)


def cmd_ver_cmp(a: argparse.Namespace) -> None:
    x, y = ver_tuple(a.a), ver_tuple(a.b)
    print(-1 if x < y else (1 if x > y else 0))


def cmd_json_get(a: argparse.Namespace) -> None:
    try:
        v = json.loads(Path(a.file).read_text(encoding="utf-8")).get(a.key)
    except Exception:
        sys.exit(1)
    if v is None:
        sys.exit(1)
    print(v)


def cmd_run_as(a: argparse.Namespace) -> None:
    """Drop root to `user` and exec the command (no su/runuser needed on minimal systems)."""
    import pwd

    pw = pwd.getpwnam(a.user)
    os.initgroups(pw.pw_name, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ.update(HOME=pw.pw_dir, USER=pw.pw_name, LOGNAME=pw.pw_name)
    os.chdir("/")
    os.execvp(a.argv[0], a.argv)


def cmd_spawn(a: argparse.Namespace) -> None:
    """Start argv detached in its own session (like `setsid nohup`), pid -> pidfile."""
    pid = os.fork()
    if pid:
        Path(a.pidfile).write_text(f"{pid}\n", encoding="ascii")
        return
    os.setsid()
    os.chdir("/")
    fd_in = os.open(os.devnull, os.O_RDONLY)
    fd_out = os.open(a.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    os.dup2(fd_in, 0)
    os.dup2(fd_out, 1)
    os.dup2(fd_out, 2)
    os.closerange(3, 1024)
    os.execvp(a.argv[0], a.argv)


def cmd_tar_create(a: argparse.Namespace) -> None:
    with tarfile.open(a.out, "w:gz") as tf:
        for p in sorted(Path(a.dir).iterdir()):
            tf.add(p, arcname=p.name)
    os.chmod(a.out, 0o600)


def cmd_tar_list(a: argparse.Namespace) -> None:
    with tarfile.open(a.file, "r:*") as tf:
        for name in tf.getnames():
            print(name)


def cmd_admin_status(a: argparse.Namespace) -> None:
    """Does the bootstrap admin still use the initial password from .env?

    Prints `state` (initial | changed | none | unknown), `username`, `password`
    as KEY=VALUE lines. `changed` also covers an admin that already existed in a
    reused database: NetX only applies the bootstrap password to an empty app_user.
    """
    env = read_env(Path(a.env))
    user = (env.get("NETX_BOOTSTRAP_ADMIN_USERNAME") or "admin").strip() or "admin"
    pw = (env.get("NETX_BOOTSTRAP_ADMIN_PASSWORD") or "admin123").strip() or "admin123"
    state = "unknown"
    try:
        import bcrypt  # type: ignore
        import psycopg  # type: ignore

        p = url_parts(env.get("NETX_DATABASE_URL", ""))
        with psycopg.connect(host=p["host"], port=int(p["port"]), user=p["user"], password=p["password"],
                             dbname=p["db"], connect_timeout=8) as conn:
            row = conn.execute("SELECT password_hash FROM app_user WHERE username = %s", (user,)).fetchone()
        if row is None:
            state = "none"
        else:
            ok = bcrypt.checkpw(pw.encode("utf-8"), str(row[0] or "").encode("ascii"))
            state = "initial" if ok else "changed"
    except Exception as exc:  # table missing before first start, DB down, ...
        state = "none" if "app_user" in str(exc) and "does not exist" in str(exc) else "unknown"
    print(f"state={state}")
    print(f"username={user}")
    print(f"password={pw}")


def cmd_check_update(a: argparse.Namespace) -> None:
    cfg = Cfg(read_env(Path(a.env)))
    cur = current_version(Path(a.prefix))
    best, errors = find_update(cfg, a.arch, a.quiet)
    if best is None:
        die("update_check_failed", "; ".join(errors), rc=2)
    newer = ver_tuple(best["latest"]) > ver_tuple(cur)
    out = {"current": cur, "update_available": newer, **best}
    print(json.dumps(out, ensure_ascii=False))


def cmd_fetch_update(a: argparse.Namespace) -> None:
    """Download + verify + extract. Prints the extracted package directory."""
    cfg = Cfg(read_env(Path(a.env)))
    info = json.loads(a.info)
    work = Path(a.workdir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    name = str(info.get("name") or "") or Path(urllib.parse.urlparse(info["url"]).path).name
    if not asset_name_re(a.arch).match(name):
        die("unexpected_asset_name", name)
    pkg = work / name
    print(f"==> Downloading {info['url']}", file=sys.stderr)
    download(info["url"], pkg, cfg)
    expected = (info.get("sha256") or "").lower()
    if not expected:
        sha_url = info.get("sha256_url") or (info["url"] + ".sha256")
        try:
            sha_tmp = work / (name + ".sha256")
            download(sha_url, sha_tmp, cfg)
            expected = sha_tmp.read_text(encoding="ascii", errors="replace").strip().split()[0].lower()
        except Exception as exc:
            die("sha256_unavailable", f"{sha_url}: {exc}")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        die("invalid_sha256_metadata", expected[:80])
    actual = sha256_file(pkg)
    if actual != expected:
        die("sha256_mismatch", f"got {actual} expected {expected}")
    print("==> sha256 OK", file=sys.stderr)
    print(safe_extract(pkg, work / "pkg"))


def cmd_extract(a: argparse.Namespace) -> None:
    """Offline update: verify optional <file>.sha256 next to the tarball, then extract."""
    tar_path = Path(a.file)
    if not tar_path.is_file():
        die("file_not_found", str(tar_path))
    if not asset_name_re(a.arch).match(tar_path.name):
        die("unexpected_asset_name", f"{tar_path.name} (expected netx-<ver>-linux-{a.arch}.tar.gz)")
    side = tar_path.with_name(tar_path.name + ".sha256")
    if side.is_file():
        expected = side.read_text(encoding="ascii", errors="replace").strip().split()[0].lower()
        if sha256_file(tar_path) != expected:
            die("sha256_mismatch", str(side))
        print("==> sha256 OK", file=sys.stderr)
    work = Path(a.workdir)
    if work.exists():
        shutil.rmtree(work)
    print(safe_extract(tar_path, work / "pkg"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="netx_admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("env-get"); s.add_argument("file"); s.add_argument("key"); s.set_defaults(fn=cmd_env_get)
    s = sub.add_parser("env-set"); s.add_argument("file"); s.add_argument("pairs", nargs="*")
    s.add_argument("--unset", action="append"); s.set_defaults(fn=cmd_env_set)
    s = sub.add_parser("env-export"); s.add_argument("file"); s.set_defaults(fn=cmd_env_export)
    s = sub.add_parser("port-free"); s.add_argument("port"); s.add_argument("--host", default="0.0.0.0")
    s.set_defaults(fn=cmd_port_free)
    s = sub.add_parser("norm-bind"); s.add_argument("value"); s.set_defaults(fn=cmd_norm_bind)
    s = sub.add_parser("valid-port"); s.add_argument("port"); s.set_defaults(fn=cmd_valid_port)
    s = sub.add_parser("gen-secret"); s.add_argument("kind", choices=["fernet", "password"]); s.set_defaults(fn=cmd_gen_secret)
    s = sub.add_parser("build-url")
    for k in ("user", "password", "host", "port", "db"):
        s.add_argument(k)
    s.set_defaults(fn=cmd_build_url)
    s = sub.add_parser("url-port"); s.add_argument("url"); s.add_argument("port"); s.set_defaults(fn=cmd_url_port)
    s = sub.add_parser("url-part"); s.add_argument("url")
    s.add_argument("part", choices=["user", "password", "host", "port", "db"]); s.set_defaults(fn=cmd_url_part)
    s = sub.add_parser("probe-db"); s.add_argument("url"); s.set_defaults(fn=cmd_probe_db)
    s = sub.add_parser("pg-conf"); s.add_argument("conf"); s.add_argument("--set", action="append")
    s.add_argument("--get-port", action="store_true"); s.set_defaults(fn=cmd_pg_conf)
    s = sub.add_parser("wait-health"); s.add_argument("url"); s.add_argument("--timeout", type=float, default=90)
    s.set_defaults(fn=cmd_wait_health)
    s = sub.add_parser("health"); s.add_argument("url"); s.set_defaults(fn=cmd_health)
    s = sub.add_parser("ver-cmp"); s.add_argument("a"); s.add_argument("b"); s.set_defaults(fn=cmd_ver_cmp)
    s = sub.add_parser("json-get"); s.add_argument("file"); s.add_argument("key"); s.set_defaults(fn=cmd_json_get)
    s = sub.add_parser("check-update"); s.add_argument("--env", required=True); s.add_argument("--prefix", required=True)
    s.add_argument("--arch", default="x86_64"); s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_check_update)
    s = sub.add_parser("fetch-update"); s.add_argument("--env", required=True); s.add_argument("--info", required=True)
    s.add_argument("--workdir", required=True); s.add_argument("--arch", default="x86_64")
    s.set_defaults(fn=cmd_fetch_update)
    s = sub.add_parser("extract"); s.add_argument("file"); s.add_argument("--workdir", required=True)
    s.add_argument("--arch", default="x86_64"); s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("admin-status"); s.add_argument("--env", required=True); s.set_defaults(fn=cmd_admin_status)
    s = sub.add_parser("run-as"); s.add_argument("user"); s.add_argument("argv", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_run_as)
    s = sub.add_parser("spawn"); s.add_argument("log"); s.add_argument("pidfile")
    s.add_argument("argv", nargs=argparse.REMAINDER); s.set_defaults(fn=cmd_spawn)
    s = sub.add_parser("tar-create"); s.add_argument("out"); s.add_argument("dir"); s.set_defaults(fn=cmd_tar_create)
    s = sub.add_parser("tar-list"); s.add_argument("file"); s.set_defaults(fn=cmd_tar_list)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
