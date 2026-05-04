# 参与贡献

外部协作默认通过 **Fork → 分支 → Pull Request**；仓库所有者审阅合并后，你在本机 `git pull` 即可同步。

## 流程摘要

1. Fork 本仓库，克隆你的 Fork。
2. 基于当前默认分支（一般为 `main`）创建主题分支，例如 `fix/...` 或 `feat/...`。
3. 提交并推送到你的 Fork，在 GitHub 上向本仓库发起 PR，说明变更内容与测试方式。

## 合并前注意

- 不要提交 `.env`、数据库口令、生产 URL 等敏感信息；以 `.env.example` / 文档中的占位符为准。
- Python 使用仓库约定的虚拟环境（见 `README.md`「Environment setup」）。
- 若改动 API、数据库或前端类型，请同步更新对应层（后端 schema / 前端 `types`）并尽量本地验证。

## 问题反馈

请使用 GitHub Issues（仓库内提供模板），写清复现步骤与环境。

若仓库暂不接收外部 PR，可在 README 中说明；本文档仍可供团队内部参考。
