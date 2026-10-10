// Run against a local Vite server. All API responses are fixtures; no device or DB is touched.
// NETX_PLAYWRIGHT_MODULE can point at a bundled Playwright package.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.NETX_PLAYWRIGHT_MODULE || "playwright");
const base = process.env.NETX_TEST_URL || "http://127.0.0.1:5179";
const output = path.resolve(process.env.NETX_TEST_OUTPUT || "../docs/reviews/assets");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.NETX_TEST_BROWSER || "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const fields = ["neighbor", "vrf", "remote_as", "state"];
const sheet = {
  sheet_id: "peer", title: "BGP 邻居", metric_id: "bgp_peer",
  key_fields: ["neighbor", "vrf"], iface_fields: [], compare_fields: ["remote_as", "state"],
  status: "done", summary: { changed: 1, removed: 1, added: 1, unchanged: 2, unchanged_listed: 2 },
};
const side = (label, ne_name, time) => ({ label, ne_name, started_at: time, ne_ip: "10.0.0.1" });
const run = {
  id: "run-1", status: "success", job_id: "job-1", config_snapshot_version: 1, created_at: "2026-10-10T10:08:00Z",
  before: side("before", "PE-BEFORE", "2026-10-10T09:00:00Z"),
  after: side("after", "PE-AFTER", "2026-10-10T10:00:00Z"),
  sheets: [sheet],
  summary: { ...sheet.summary, before_count: 4, after_count: 4, pass_rate: 50, duration_ms: 1250,
    sheet_cards: [{ ...sheet, ...sheet.summary, before_count: 4, after_count: 4, pass_rate: 50 }] },
};
const job = { id: "job-1", name: "割接前后 · 业务验收", template_id: "tpl", mode: "manual", status: "ready",
  before_task_id: "t1", after_task_id: "t2", before_batch_id: "b", after_batch_id: "a" };
const row = (neighbor, state, remote_as = "64512") => ({ neighbor, vrf: "core", state, remote_as });
const diffs = [
  { kind: "changed", key: { neighbor: "10.2.1.1", vrf: "core" },
    before: row("10.2.1.1", "Established"), after: row("10.2.1.1", "Idle"),
    changes: { state: { before: "Established", after: "Idle" } } },
  { kind: "removed", key: { neighbor: "10.2.1.2", vrf: "core" }, before: row("10.2.1.2", "Established"), after: null, changes: {} },
  { kind: "added", key: { neighbor: "10.2.1.3", vrf: "core" }, before: null, after: row("10.2.1.3", "Established"), changes: {} },
  { kind: "unchanged", key: { neighbor: "10.2.1.4", vrf: "core" }, before: row("10.2.1.4", "Established"), after: row("10.2.1.4", "Established"), changes: {} },
];
let failDiffs = false;
let active = false;
let legacy = false;
let pollCounts = { runs: 0, detail: 0 };
await page.route("**/v1/**", async (route) => {
  const url = new URL(route.request().url());
  const p = url.pathname;
  let data = {};
  if (p === "/v1/auth/me") data = { user: { id: "test", username: "test", role: "admin", is_active: true } };
  else if (p.endsWith("/compare/jobs")) data = { items: [job, { ...job, id: "job-slow", name: "慢任务" }] };
  else if (p.endsWith("/compare/templates")) data = { items: [{ id: "tpl", name: "割接验收模板", metrics: [sheet] }] };
  else if (p.endsWith("/compare/metrics")) data = { items: [{ metric_id: "bgp_peer", fields: fields.map((name) => ({ name, display_name: name, dtype: "string", is_key: sheet.key_fields.includes(name) })) }] };
  else if (p.endsWith("/tasks")) data = { items: [{ id: "t1", ne_name: "PE-BEFORE", ne_ip: "10.0.0.1" }, { id: "t2", ne_name: "PE-AFTER", ne_ip: "10.0.0.2" }] };
  else if (p.endsWith("/batches")) data = { items: [{ id: p.includes("t1") ? "b" : "a", status: "success", row_count: 4 }] };
  else if (p.endsWith("/runs") && p.includes("/jobs/")) {
    pollCounts.runs++;
    if (p.includes("job-slow")) await new Promise((resolve) => setTimeout(resolve, 900));
    data = { items: [{ ...run, status: active ? "running" : "success" }] };
  } else if (p.endsWith("/diffs")) {
    if (failDiffs) { await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "测试：连接暂时中断" }) }); return; }
    const kind = url.searchParams.get("kind") || "diff";
    const kw = url.searchParams.get("kw") || "";
    const items = diffs.filter((d) => (kind === "all" || (kind === "diff" ? ["removed", "changed"].includes(d.kind) : d.kind === kind)) && JSON.stringify(d).includes(kw));
    data = { items, total: items.length, page: 1, page_size: 100, source: "stored", truncated: false };
  } else if (p.endsWith("/runs/run-1")) {
    pollCounts.detail++;
    data = { ...run, config_snapshot_version: legacy ? null : 1, status: active ? "running" : "success" };
  } else if (p.endsWith("/mappings")) data = { items: [] };
  else data = { items: [] };
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
});

try {
  await page.goto(`${base}/network/cutover/biz-compare`);
  await page.getByRole("row").filter({ hasText: "割接前后 · 业务验收" }).getByRole("button", { name: "详情", exact: true }).click();
  await page.getByRole("button", { name: "比对结果", exact: true }).click();
  await page.locator(".bs-cmp-diff-table tbody tr.bs-cmp-row").first().waitFor();
  assert.equal(await page.locator(".bs-cmp-diff-table tbody tr.bs-cmp-row").count(), 2);
  assert.equal(await page.locator(".bs-cmp-board__config").textContent(), "执行配置已保存");
  await page.screenshot({ path: path.join(output, "biz-compare-desktop.png"), fullPage: true });

  const search = page.locator(".bs-cmp-filter-bar input");
  failDiffs = true;
  await search.fill("故障");
  await page.getByText("对比明细加载失败", { exact: true }).waitFor();
  assert.equal(await page.locator(".bs-cmp-row").count(), 0);
  await page.screenshot({ path: path.join(output, "biz-compare-error.png"), fullPage: true });
  failDiffs = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await page.getByText("当前筛选条件下没有匹配结果，可清空筛选或切换结果类型。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "清空筛选", exact: true }).click();
  await page.locator(".bs-cmp-row").first().waitFor();

  await page.setViewportSize({ width: 768, height: 1000 });
  await page.screenshot({ path: path.join(output, "biz-compare-narrow.png"), fullPage: true });
  const fits = await page.locator(".bs-cmp-strip").evaluate((element) => element.scrollWidth <= element.clientWidth + 1);
  assert.ok(fits, "filter toolbar must fit its available width");
  await page.setViewportSize({ width: 390, height: 1000 });
  assert.ok(await page.locator(".bs-cmp-strip").evaluate((element) => element.scrollWidth <= element.clientWidth + 1));
  assert.ok(await page.locator(".bs-cmp-board__toolbar").evaluate((element) => element.scrollWidth <= element.clientWidth + 1));
  await page.setViewportSize({ width: 768, height: 1000 });

  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("row").filter({ hasText: "慢任务" }).getByRole("button", { name: "详情", exact: true }).click();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("row").filter({ hasText: "割接前后 · 业务验收" }).getByRole("button", { name: "详情", exact: true }).click();
  await page.getByRole("button", { name: "比对结果", exact: true }).click();
  await page.locator(".bs-cmp-row").first().waitFor();
  await page.waitForTimeout(1100);
  assert.equal(await page.locator(".bs-cmp-row").count(), 2, "slow old job must not change the current tab");

  legacy = true;
  await page.reload();
  await page.getByRole("row").filter({ hasText: "割接前后 · 业务验收" }).getByRole("button", { name: "详情", exact: true }).click();
  await page.getByRole("button", { name: "比对结果", exact: true }).click();
  await page.getByText("旧记录：未保存执行配置", { exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, "biz-compare-legacy.png"), fullPage: true });

  active = true;
  await page.reload();
  await page.getByRole("row").filter({ hasText: "割接前后 · 业务验收" }).getByRole("button", { name: "详情", exact: true }).click();
  await page.locator(".bs-cmp-runs").waitFor();
  pollCounts = { runs: 0, detail: 0 };
  await page.waitForTimeout(5200);
  assert.ok(pollCounts.runs <= 3 && pollCounts.detail <= 3, JSON.stringify(pollCounts));
  assert.ok(pollCounts.runs >= 2 && pollCounts.detail >= 2, "active run should keep polling");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: ["result layout", "saved execution settings", "legacy settings warning", "error clears stale rows", "retry", "clear filters", "768px toolbar", "old job response ignored", "single poller", "no runtime errors"], pollCounts, output }));
} finally {
  await browser.close();
}
