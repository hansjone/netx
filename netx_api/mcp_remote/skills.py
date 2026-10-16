"""Publish netx ops/topology/biz-monitor skills via MCP resources / prompts / getNetxSkill."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import (
    ALL_TOOLS,
    GROUP_BIZ,
    GROUP_OPS,
    GROUP_TOPOLOGY,
    VALID_GROUPS,
    capability_tools_for_request,
)

# MCP group name → skills/ subdirectory (explicit; biz-state is intentionally absent).
GROUP_DIR: dict[str, str] = {
    GROUP_OPS: "ops",
    GROUP_TOPOLOGY: "topology",
    GROUP_BIZ: "biz-monitor",
}
DIR_TO_GROUP: dict[str, str] = {v: k for k, v in GROUP_DIR.items()}

URI_PREFIX = "netx://skills/"
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".toml"})
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_DESC_RE = re.compile(
    r"^description:\s*(?:>-?\s*)?(?:\|[+-]?\s*)?(.*?)(?=\n[a-zA-Z_][\w-]*:|\Z)",
    re.DOTALL | re.MULTILINE,
)

GET_NETX_SKILL_NAME = "getNetxSkill"
_BASE_INSTRUCTIONS = (
    "netx remote MCP: ops / topology / bizMonitor tools. "
    "Filter with ?groups=ops,topology,bizMonitor. "
    "Authorize with Bearer MCP token or user JWT."
)
_CATALOG_HINT = (
    "做这类任务前先调用 getNetxSkill 读取对应 skill，也可以读 netx://skills/... 资源。"
)
_INSTRUCTIONS_MAX = 1500


@dataclass(frozen=True)
class SkillFile:
    group: str
    group_dir: str
    skill: str
    rel_path: str  # posix relative path inside the skill folder
    abs_path: Path

    @property
    def uri(self) -> str:
        return f"{URI_PREFIX}{self.group_dir}/{self.skill}/{self.rel_path}"

    @property
    def entry(self) -> bool:
        return self.rel_path == "SKILL.md"


def skills_root() -> Path:
    """Locate skills directory: NETX_SKILLS_ROOT, else repo/package root ``skills/``."""
    env = str(os.environ.get("NETX_SKILLS_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # netx_api/mcp_remote/skills.py → parents[2] = package/repo root
    return Path(__file__).resolve().parents[2] / "skills"


def _is_text_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES


# abs_path -> (mtime_ns, size, description); SKILL.md is re-parsed only when it changes.
_DESC_CACHE: dict[Path, tuple[int, int, str]] = {}


def _cached_description(skill_file: "SkillFile") -> str:
    try:
        st = skill_file.abs_path.stat()
    except OSError:
        return ""
    hit = _DESC_CACHE.get(skill_file.abs_path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        desc = _parse_description(read_skill_text(skill_file))
    except OSError:
        desc = ""
    _DESC_CACHE[skill_file.abs_path] = (st.st_mtime_ns, st.st_size, desc)
    return desc


def _parse_description(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return ""
    block = m.group(1)
    dm = _DESC_RE.search(block)
    if not dm:
        return ""
    raw = dm.group(1).strip()
    # Fold YAML folded/literal block lines into a single paragraph.
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return " ".join(lines).strip().strip("\"'")


def _first_sentence(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return ""
    for sep in ("。", ". ", "！", "!", "？", "?"):
        if sep in text:
            part = text.split(sep, 1)[0].strip()
            if sep == "。":
                return part + "。"
            if sep.startswith("."):
                return part + "."
            return part + sep[0]
    return text


def scan_skill_files(*, root: Path | None = None) -> list[SkillFile]:
    """Read disk every call. Only mapped group dirs; no path traversal."""
    base = root if root is not None else skills_root()
    out: list[SkillFile] = []
    if not base.is_dir():
        return out
    for group, group_dir in GROUP_DIR.items():
        group_path = base / group_dir
        if not group_path.is_dir():
            continue
        for skill_dir in sorted(group_path.iterdir(), key=lambda p: p.name.lower()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_name = skill_dir.name
            for path in sorted(skill_dir.rglob("*")):
                if not _is_text_file(path):
                    continue
                try:
                    rel = path.relative_to(skill_dir).as_posix()
                except ValueError:
                    continue
                if ".." in rel.split("/"):
                    continue
                out.append(
                    SkillFile(
                        group=group,
                        group_dir=group_dir,
                        skill=skill_name,
                        rel_path=rel,
                        abs_path=path.resolve(),
                    )
                )
    return out


def visible_skill_groups(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
) -> frozenset[str]:
    """A skill group is visible iff the request has ≥1 visible tool in that group."""
    tools = capability_tools_for_request(scopes, groups)
    return frozenset(str(t.get("_group")) for t in tools if t.get("_group") in VALID_GROUPS)


def list_visible_skill_files(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> list[SkillFile]:
    allowed = visible_skill_groups(scopes, groups)
    return [f for f in scan_skill_files(root=root) if f.group in allowed]


def list_visible_skills(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Unique skills as (name, group, description) ordered by group then name."""
    files = list_visible_skill_files(scopes, groups, root=root)
    by_key: dict[tuple[str, str], tuple[str, str, str]] = {}
    for f in files:
        key = (f.group, f.skill)
        if key in by_key and not f.entry:
            continue
        desc = _cached_description(f) if f.entry else ""
        by_key[key] = (f.skill, f.group, desc)
    order = {GROUP_OPS: 0, GROUP_TOPOLOGY: 1, GROUP_BIZ: 2}
    return sorted(by_key.values(), key=lambda x: (order.get(x[1], 9), x[0].lower()))


def read_skill_text(skill_file: SkillFile) -> str:
    raw = skill_file.abs_path.read_text(encoding="utf-8")
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def resolve_uri(
    uri: str,
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> SkillFile | None:
    """Resolve only against the visible list (no arbitrary path join)."""
    target = str(uri or "").strip()
    for f in list_visible_skill_files(scopes, groups, root=root):
        if f.uri == target:
            return f
    return None


def resolve_skill_file(
    skill_name: str,
    rel_path: str,
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> SkillFile | None:
    name = str(skill_name or "").strip()
    rel = (str(rel_path or "").strip() or "SKILL.md").replace("\\", "/")
    if not name or ".." in rel.split("/") or rel.startswith("/"):
        return None
    for f in list_visible_skill_files(scopes, groups, root=root):
        if f.skill == name and f.rel_path == rel:
            return f
    return None


def unavailable_tools(
    group: str,
    scopes: frozenset[str] | list[str] | None,
) -> list[str]:
    """Tools of ``group`` the skill may mention but this token cannot call."""
    granted = {str(t["name"]) for t in capability_tools_for_request(scopes, frozenset({group}))}
    return [
        str(t["name"])
        for t in ALL_TOOLS
        if t.get("_group") == group and str(t["name"]) not in granted
    ]


def unavailable_note(group: str, scopes: frozenset[str] | list[str] | None) -> str:
    names = unavailable_tools(group, scopes)
    if not names:
        return ""
    return (
        "\n---\n当前 token 无权使用以下工具，本 skill 中涉及它们的步骤请跳过，不要调用："
        + ", ".join(names)
        + "\n"
    )


def resource_dict(
    skill_file: SkillFile,
    *,
    description: str | None = None,
    scopes: frozenset[str] | list[str] | None = None,
) -> dict[str, Any]:
    desc = description
    if desc is None and skill_file.entry:
        desc = _cached_description(skill_file)
    out: dict[str, Any] = {
        "uri": skill_file.uri,
        "name": skill_file.rel_path,
        "mimeType": "text/markdown",
        "_meta": {
            "netx": {
                "group": skill_file.group,
                "skill": skill_file.skill,
                "entry": skill_file.entry,
                **(
                    {"unavailableTools": unavailable_tools(skill_file.group, scopes)}
                    if skill_file.entry
                    else {}
                ),
            }
        },
    }
    if skill_file.entry and desc:
        out["description"] = desc
    return out


def build_instructions(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> str:
    """Dynamic skill catalog appended to base instructions (≤1500 chars)."""
    skills = list_visible_skills(scopes, groups, root=root)
    lines = [_BASE_INSTRUCTIONS, ""]
    if skills:
        lines.append("Skills:")
        for name, group, desc in skills:
            lines.append(f"- {name}（{group}）：{_first_sentence(desc) or name}")
        lines.append(_CATALOG_HINT)
    else:
        lines.append("Skills: （当前 token / groups 下无可见 skill）")
    text = "\n".join(lines)
    if len(text) <= _INSTRUCTIONS_MAX:
        return text
    # Truncate catalog lines, keep base + hint.
    head = _BASE_INSTRUCTIONS + "\n\nSkills:\n"
    tail = "\n" + _CATALOG_HINT
    budget = _INSTRUCTIONS_MAX - len(head) - len(tail)
    body_lines: list[str] = []
    used = 0
    for name, group, desc in skills:
        line = f"- {name}（{group}）：{_first_sentence(desc) or name}"
        need = len(line) + (1 if body_lines else 0)
        if used + need > budget:
            break
        body_lines.append(line)
        used += need
    return head + "\n".join(body_lines) + tail


def get_netx_skill_tool_dict() -> dict[str, Any]:
    return {
        "name": GET_NETX_SKILL_NAME,
        "description": (
            "读取 netx 运维 skill 文件原文（默认 SKILL.md）。"
            "做 ops / topology / bizMonitor 类任务前先调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名，例如 netx-ops",
                },
                "file": {
                    "type": "string",
                    "description": "相对路径，默认 SKILL.md",
                },
            },
            "required": ["name"],
        },
        "annotations": {
            "readOnlyHint": True,
            "idempotentHint": True,
            "destructiveHint": False,
        },
        "_meta": {"netx": {"group": "meta"}},
        "_group": "meta",
    }


def should_advertise_get_netx_skill(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> bool:
    return any(f.entry for f in list_visible_skill_files(scopes, groups, root=root))


def available_skill_names(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> list[str]:
    return [name for name, _g, _d in list_visible_skills(scopes, groups, root=root)]


def call_get_netx_skill(
    args: dict[str, Any],
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    name = str((args or {}).get("name") or "").strip()
    rel = str((args or {}).get("file") or "SKILL.md").strip() or "SKILL.md"
    available = available_skill_names(scopes, groups, root=root)
    hint = "当前可用 skill：" + (", ".join(available) if available else "（无）")
    if not name:
        return {
            "content": [{"type": "text", "text": f"缺少 name 参数。{hint}"}],
            "isError": True,
        }
    skill_file = resolve_skill_file(name, rel, scopes, groups, root=root)
    if skill_file is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"skill 不可见或不存在：name={name!r} file={rel!r}。{hint}",
                }
            ],
            "isError": True,
        }
    try:
        text = read_skill_text(skill_file)
    except OSError:
        return {
            "content": [{"type": "text", "text": f"读取失败。{hint}"}],
            "isError": True,
        }
    if skill_file.entry:
        siblings = [
            f.rel_path
            for f in list_visible_skill_files(scopes, groups, root=root)
            if f.skill == skill_file.skill and f.rel_path != "SKILL.md"
        ]
        if siblings:
            text = text.rstrip() + "\n\n---\n其他文件：\n" + "\n".join(f"- {s}" for s in siblings) + "\n"
        else:
            text = text.rstrip() + "\n\n---\n其他文件：（无）\n"
        text += unavailable_note(skill_file.group, scopes)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def prompt_skill_md_text(
    skill_name: str,
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
    *,
    root: Path | None = None,
) -> str | None:
    skill_file = resolve_skill_file(skill_name, "SKILL.md", scopes, groups, root=root)
    if skill_file is None:
        return None
    try:
        body = read_skill_text(skill_file)
    except OSError:
        return None
    return (
        body.rstrip()
        + "\n\n其他文件用 getNetxSkill 读取。\n"
        + unavailable_note(skill_file.group, scopes)
    )
