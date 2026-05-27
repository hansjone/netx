# NetX Web 前端规范

## 技术栈

- React 19 + TypeScript + Vite
- React Router（工作台 + 模块页）
- TanStack Query（服务端状态）
- 自研 i18n（`src/i18n/`，无额外依赖）

## 目录结构

```
src/
  config/modules.ts      # 模块注册表（工作台卡片、路由、标题 i18n）
  constants/queryKeys.ts # React Query key
  components/            # 可复用 UI（无业务 API 调用）
  hooks/                 # 通用 hooks（Toast、窗口注册）
  i18n/                  # 文案 zh / en
  layout/                # AppLayout、ErrorBoundary
  pages/                 # 页面（按路由）
  services/api.ts        # HTTP 封装与 API 函数
  types.ts               # 与后端契约类型
  utils/
    tabChannel.ts        # 跨标签页聚焦（BroadcastChannel）
    workbench.ts         # 返回工作台
    moduleWindows.ts     # 打开/聚焦模块页签
    display.ts           # 纯展示辅助
```

## 路由与模块

| 路径 | 页面 | moduleId |
|------|------|----------|
| `/` | 工作台 | — |
| `/ume` | UME 同步 | `ume` |

**新增模块只需改 `config/modules.ts`：**

1. 在 `MODULES` 增加一项（`moduleId`、`path`、`section`、i18n key、`iconTone`）
2. 在 `App.tsx` 增加 `<Route path="..." element={...} />`
3. 在 `i18n/zh.ts` 与 `en.ts` 补充文案
4. 工作台卡片由 `modulesInSection` 自动生成，点击走 `openOrFocusModule`

## 跨标签页导航

- **工作台 → 模块**：`openOrFocusModule`（同 `moduleId` 仅一个标签，已打开则聚焦）
- **模块 → 工作台**：顶栏四格图标，`returnToWorkbench`（聚焦原工作台标签；已关闭则新开）
- 实现：`utils/tabChannel.ts` + 命名窗口 `netx-module-{id}` / `netx-workbench`

## i18n

- 所有面向用户的文案走 `useI18n().t("dotted.key")`
- 表头/字段名等技术列可保持英文
- 语言存在 `localStorage`（`netx-locale`），顶栏 ⋯ 切换

## 数据请求

- API 仅写在 `services/api.ts`
- Query key 集中在 `constants/queryKeys.ts`
- 失效缓存用 prefix key（如 `queryKeys.umeSyncStatusAll`）
- 顶栏连接状态：`App` 轮询 `GET /v1/integrations/status`（5s），展示 **netx api** 与 **oclaw bridge**（含延迟 / 错误类型）

## Toast

- 使用 `ToastProvider`（`main.tsx`）+ `useToast()`
- 禁止页面内单独维护一套 toast 状态

## 样式

- 全局样式：`index.css`（工作台浅色主题）
- 不使用 CSS Modules；类名 BEM 风格：`app-brand__logo`、`wb-card`

## 构建

```bash
cd web && npm run build
```

开发：`npm run dev`（需后端 API 或 Vite proxy 配置）
