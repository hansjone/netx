# netx MCP · topology 组

拓扑树、画布（views）、Fabric 与布局工具，通过同一远程端点提供：

```text
http://127.0.0.1:8890/mcp?groups=topology
```

鉴权与总览见 [MCP.md](./MCP.md)。需要 `ne:read` / `ne:write` 等 scope（写工具含新建文件夹、改节点、布局写入等）。

布局算法在 netx 进程内执行（原 `netx-topology-mcp` 逻辑已迁入 `netx_api/mcp_remote/topology/`）。异步布局 job 仍返回 job id 后轮询。
