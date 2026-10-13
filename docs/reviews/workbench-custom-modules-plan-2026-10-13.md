# 工作台功能卡片自定义 — 实现方案（2026-10-13）

## 目标

工作台功能区（UME 同步、网元管理、网络管理、拓扑、WebCRT…）以后会越来越多，每个人关心的不一样。
让每个用户自己选择：**显示哪些卡片、按什么顺序**；设置跟着账号走（换电脑/换浏览器仍生效）。

## 现状

- 卡片注册表：`web/src/config/modules.ts:33` `MODULES`，按 `section` 分 `monitoring / operations / system`。
- 过滤逻辑：`web/src/features/workbench/WorkbenchPage.tsx:323` `visibleBySection` —— 只按权限（`adminOnly` / `requiredScope`）和 `workbenchHidden` 过滤。
- 渲染：`web/src/features/workbench/components/WorkbenchModules.tsx:23`，operations 是大磁贴网格，system 是底部一排小链接。
- 后端无任何“用户偏好”存储；`AppUser`（`netx_api/models/auth.py:13`）只有账号字段。

## 设计决定

| 项 | 决定 | 理由 |
|---|---|---|
| 可自定义范围 | 只做 **operations 区的磁贴**（含 monitoring 合并进来的 UME 同步）。system 区链接行保持固定 | 系统管理入口少且是管理类，隐藏后反而找不到；需求说的是“功能卡片” |
| 存储位置 | **服务端，按用户**：`app_user.prefs` JSON 列 | “每个人不一样”= 按账号；localStorage 只按浏览器，换机就丢 |
| 本地缓存 | 同时写一份 localStorage（key 带 user id），首屏先用缓存渲染，避免卡片闪烁 | 首屏体验 |
| 数据结构 | `{ order: string[], hidden: string[] }`，存的是“隐藏了哪些”而不是“显示哪些” | 以后新增模块默认对所有人出现，不需要每人手动开启 |
| 新模块默认隐藏 | `ModuleDefinition` 增加可选 `defaultHidden?: boolean`；不在 `order` 里的这类模块视为隐藏 | 给以后“小众功能”留开关 |
| 权限 | 没权限的模块永远不出现在卡片区，也不出现在“可添加”列表；偏好里残留的 id 静默忽略 | 偏好不能绕过权限 |
| 交互 | 卡片区右上角一个小齿轮 → 进入**原地编辑模式**：已显示卡片可拖拽排序、右上角 × 移除；下方出现“可添加”托盘（灰显卡片，点击加入）；按钮只有「恢复默认」「完成」 | 和 iOS 主屏编辑一致，最少文案；不弹窗 |
| 拖拽实现 | HTML5 原生 drag & drop，不引入新依赖 | 卡片数量小（<20），原生够用；项目里没有 dnd 库 |
| 保存时机 | 点「完成」时一次 PUT；失败则 toast 并保留编辑状态 | 避免拖动过程中频繁请求 |
| 全部隐藏 | 允许，但功能区显示一个空态（只有齿轮 + 一句“添加功能”） | 不阻止用户，但要能找回 |

## 任务

### 后端

**B1 — `app_user.prefs` 列**
- `netx_api/models/auth.py:13` `AppUser` 加 `prefs: Mapped[dict] = mapped_column(_JsonType, default=dict)`。
- 新迁移 `alembic/versions/20261013_user_prefs.py`，`down_revision = "20260922_biz_state_queued"`，`add_column("app_user", sa.Column("prefs", JSON, nullable=False, server_default="{}"))`。

**B2 — 读写接口**（`netx_api/auth_router.py`，紧跟 `api_me` 之后，约 :273）
- `GET /v1/auth/me/prefs` → `{"prefs": {...}}`
- `PUT /v1/auth/me/prefs/{key}`，body 为该 key 的值；`key` 白名单，目前只允许 `workbench`。
- 校验 `workbench`：`{order: list[str], hidden: list[str]}`，每个列表 ≤ 64 项、每个 id ≤ 64 字符、只允许 `[a-z0-9-]`；其他字段丢弃。超限 422。
- 只改自己的偏好（`require_user`），不需要额外 scope；不写审计（纯 UI 偏好，避免刷屏）。
- `/v1/auth/me` 响应里顺带返回 `prefs`，前端登录后一次拿到，不多一次请求。

### 前端

**F1 — 注册表扩展** `web/src/config/modules.ts`
- `ModuleDefinition` 加 `defaultHidden?: boolean`。
- 新增 `export function customizableModules()`：operations + monitoring 中非 `workbenchHidden` 的模块（即当前 `displaySections` 里 operations 那组的来源）。

**F2 — 偏好 hook** 新建 `web/src/features/workbench/useWorkbenchLayout.ts`
- 输入：权限过滤后的可用模块列表；输出：`{ shown: ModuleDefinition[], available: ModuleDefinition[], save(order, hidden), reset() }`。
- 解析规则（纯函数 `resolveLayout(all, prefs)`，单独导出便于测试）：
  1. 先按 `prefs.order` 排已知 id，丢弃不存在/无权限的 id；
  2. 剩余模块按注册表顺序追加到末尾；
  3. 在 `hidden` 中的、或 `defaultHidden` 且不在 `order` 中的，归入 `available`。
- 数据来源：`AuthContext` 的 `me.prefs.workbench` → 没有则 localStorage 缓存 `netx.wb.layout.<userId>` → 默认。
- 保存：PUT 成功后更新 AuthContext 中的 prefs 和 localStorage。localStorage 读写全部 try/catch。

**F3 — AuthContext** `web/src/features/auth/AuthContext.tsx:48`
- `refreshMe` / `login` 把响应里的 `prefs` 存进 state，并暴露 `prefs` 与 `setPrefs`。

**F4 — 编辑模式 UI** `web/src/features/workbench/components/WorkbenchModules.tsx`
- `WorkbenchPage.tsx:334` `displaySections` 的 operations 组改为用 `useWorkbenchLayout().shown`。
- 卡片区右上角齿轮按钮（`ConsoleIcon` 加一个 `settings` 图标），`aria-label` = “自定义功能区”。
- 编辑态：
  - 磁贴轻微抖动或描边（遵循 glass 风格：**不发光**，只用 `--nx-line` 边框）、右上角 × 按钮；
  - `draggable`，`onDragStart/onDragOver/onDrop` 重排本地草稿数组；
  - 下方“可添加”托盘：灰显磁贴，点击加入末尾；托盘为空时不显示；
  - 底部两个按钮：「恢复默认」（清空草稿为默认）、「完成」（保存）；Esc = 放弃草稿退出。
  - 编辑态下点击磁贴不打开模块。
- 非编辑态 shown 为空：显示空态 + “添加功能”按钮（直接进入编辑态）。
- 样式写在 `web/src/styles/pages/workbench.css`，颜色只用现有 token（蓝黑 200–215°，无紫色）。

**F5 — 文案** `web/src/i18n/locales/{zh,en}/workbench.ts`
- 新增：`customize`（自定义功能区）、`customizeDone`（完成）、`customizeReset`（恢复默认）、`customizeAvailable`（可添加）、`customizeEmpty`（添加功能）、`customizeSaveFailed`（保存失败）。只这些，不加说明文字。

## 验收

| ID | 场景 | 期望 |
|---|---|---|
| A1 | 新用户首次登录 | 卡片与改动前完全一致（顺序、数量） |
| A2 | 隐藏「拓扑」、把「网元管理」拖到第一位，点完成 | 立即生效；刷新、换浏览器登录同一账号仍是该布局 |
| A3 | 用户 A 定制后，用户 B 登录 | B 不受影响 |
| A4 | 在 `MODULES` 新增一个模块 | 已定制过的用户自动在末尾看到它；若设了 `defaultHidden` 则出现在“可添加”里 |
| A5 | 偏好里包含无权限模块 id（如去掉用户 `ne:read` 后） | 不显示、不出现在可添加；不报错 |
| A6 | 全部移除后完成 | 显示空态，点“添加功能”可恢复 |
| A7 | 编辑中按 Esc / 保存失败（断网） | Esc 恢复原布局；失败提示并停留在编辑态 |
| A8 | `PUT /v1/auth/me/prefs/workbench` 传非法 key、超长列表、非法字符 | 404 / 422 |
| A9 | 视觉 | 编辑态无发光、无紫色，与现有 glass 风格一致 |

## 回归命令

```bash
alembic upgrade head
```

```bash
python -m pytest tests/test_auth.py tests/test_user_prefs.py -q
```

```bash
cd web && npm run lint && npm run build
```

- 新增 `tests/test_user_prefs.py`：覆盖 B2 的读写、白名单、校验、用户隔离（A3、A8）。
- `resolveLayout` 纯函数若项目有前端单测则补上（A1、A4、A5）；没有则在验收时手测。

## 以后可扩展（本次不做）

- 同一机制用 `prefs.workbench.panels` 控制右侧任务栏、网元/拓扑/调度三张统计卡的显隐。
- 管理员给角色设默认布局（`role_default_prefs`），用户未定制时用角色默认。
