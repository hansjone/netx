// Local Vite + fixture APIs only. No device connection or production database.
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
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
const errors = [], passed = [], requests = [], writes = [];
page.on("pageerror", (e) => errors.push(e.message));
let liveRunId = "live-1", uncertain = false, failBoard = false, delayedSheet = "";
const now = new Date().toISOString();
const project = { id: "p", name: "核心 PE 替换", old_task_id: "op", new_task_id: "np", old_hf_task_id: "oh", new_hf_task_id: "nh", old_baseline_batch_id: "ob", new_baseline_batch_id: "nb", monitor_template_id: "mt", hf_interval_sec: 60, status: "active", created_at: now,
  old_task: { id: "op", ne_name: "PE-OLD", ne_ip: "10.0.0.1" }, new_task: { id: "np", ne_name: "PE-NEW", ne_ip: "10.0.0.2" } };
const second = { ...project, id: "p2", name: "第二割接项目" };
const wave = { id: "wave", batch_label: "第一批 · 客户专线", status: "active", accept_status: "none" };
const sheets = ["s1", "s2"].map((sid, i) => ({ sheet_id: sid, metric_id: "arp", title: `ARP · ${i ? "专线" : "公网"}`, key_fields: ["ip"], compare_fields: ["mac"], iface_fields: [] }));
const monitor = { id: "mt", name: "业务连续性", compare_template_id: "ct", compare_template_name: "业务模板", collect_metric_ids: ["arp"], collect_metric_ids_effective: ["arp"], defaults: { custom_setting: false, threshold: 7, out_of_expect: "ignore" }, sheet_overrides: [{ metric_id: "arp", success: [{ old_groups: [["removed"]], new_groups: [["added"]], name: "客户迁移规则" }], anomaly: [] }] };
const dataset = Array.from({ length: 601 }, (_, i) => ({ id: `row-${i}`, metric_id: "arp", sheet_id: i % 2 ? "s2" : "s1", verdict: i === 600 ? "anomaly" : "migrated", color: i === 600 ? "red" : "green", in_expect: i !== 600, old_key: `10.1.${Math.floor(i / 255)}.${i % 255}`, new_key: `10.1.${Math.floor(i / 255)}.${i % 255}`, old_status: "gone", new_status: "up", rule_hit: i === 600 ? "service_content_changed" : "success#1", integrity_changes: i === 600 ? [{ field: "mac", before: "aa:bb", after: "cc:dd" }] : [] }));
function run(rid, pid = "p") {
  return { id: rid, purpose: rid === "hist" ? "manual" : "auto", created_at: now,
    summary: { progress: { ok: pid === "p2" ? 3 : 99, total: pid === "p2" ? 3 : 101 }, anomaly: 1, anomaly_outside_expect: 1, coverage_complete: !uncertain, duplicate_keys: uncertain ? 2 : 0,
      sheet_cards: sheets.map((s) => ({ ...s, progress_ok: 49, progress_total: 50, anomaly: 0, old_collected_at: now, new_collected_at: now, age_limit_sec: 180, sample_issues: uncertain ? ["sample_skew"] : [] })) } };
}
await page.route("**/v1/**", async (route) => {
  const req = route.request(), url = new URL(req.url()), p = url.pathname;
  requests.push({ p, query: Object.fromEntries(url.searchParams), method: req.method() });
  let data = { items: [] };
  if (p === "/v1/auth/me") data = { user: { id: "fixture", username: "fixture", role: "admin", is_active: true } };
  else if (req.method() === "PATCH" && p.endsWith("/monitor-templates/mt")) { data = { ...monitor, ...req.postDataJSON() }; writes.push(req.postDataJSON()); }
  else if (req.method() === "POST" && p.endsWith("/p/batches")) { writes.push(req.postDataJSON()); data = { ...wave, id: "created-wave", ...req.postDataJSON() }; }
  else if (p.endsWith("/biz-migration/projects")) data = { items: [project, second] };
  else if (p.endsWith("/monitor-templates")) data = { items: [monitor] };
  else if (p.endsWith("/compare/templates")) data = { items: [{ id: "ct", name: "业务模板", metrics: sheets }] };
  else if (p.endsWith("/compare/metrics")) data = { items: [{ metric_id: "arp", fields: [{ name: "ip" }, { name: "mac" }] }] };
  else if (p.endsWith("/baseline-expect")) data = { sheets: sheets.map((sheet) => ({ ...sheet, items: Array.from({ length: 201 }, (_, i) => ({ key: `biz-${i}`, keys: [`biz-${i}`], label: `业务 ${i}`, row: { ip: `biz-${i}` } })) })) };
  else if (p.endsWith("/red-tickets")) data = { items: [], open_count: 0 };
  else if (p.includes("/biz-migration/projects/") && p.endsWith("/batches")) data = { items: [{ ...wave, id: p.includes("/p2/") ? "wave2" : "wave" }] };
  else if (p.endsWith("/board")) {
    if (failBoard) { await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "fixture_refresh_error" }) }); return; }
    data = { project, batch: wave, run: run(url.searchParams.get("run_id") || liveRunId, p.includes("wave2") ? "p2" : "p") };
  } else if (p.endsWith("/runs")) data = { items: [run(liveRunId), run("hist")] };
  else if (p.endsWith("/diffs")) {
    let rows = dataset;
    if (url.searchParams.get("sheet_id")) rows = rows.filter((r) => r.sheet_id === url.searchParams.get("sheet_id"));
    if (url.searchParams.get("only_expect")) rows = rows.filter((r) => r.in_expect);
    if (url.searchParams.get("color")) rows = rows.filter((r) => r.color === url.searchParams.get("color"));
    if (url.searchParams.get("kw")) rows = rows.filter((r) => r.old_key.includes(url.searchParams.get("kw")));
    const offset = Number(url.searchParams.get("offset") || 0), limit = Number(url.searchParams.get("limit") || 100);
    data = { items: rows.slice(offset, offset + limit), total: rows.length };
    if (delayedSheet && url.searchParams.get("sheet_id") === delayedSheet) await new Promise((resolve) => setTimeout(resolve, 900));
  } else if (p.endsWith("/batches")) data = { items: [{ id: "ok", status: "success", started_at: now }, { id: "bad", status: "failed" }, { id: "running", status: "running" }] };
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
});
const dialog = () => page.getByRole("dialog");
const table = () => dialog().locator(".bm-board__table table");
const waitRows = (n) => page.waitForFunction((count) => document.querySelectorAll(".bm-board__table tbody tr").length === count, n);
try {
  await page.goto(`${base}/network/cutover/biz-migration`);
  const projectRow = page.locator(".pt-list table tbody tr").filter({ has: page.getByText(project.name, { exact: true }) });
  await projectRow.getByRole("button", { name: "进度看板", exact: true }).click();
  await waitRows(100);
  await dialog().getByText("范围外异常", { exact: true }).waitFor();
  assert.equal(await dialog().locator(".bm-live-kpis .is-bad").last().locator("strong").textContent(), "1");
  assert.equal(requests.filter((r) => r.p.endsWith("/diffs")).at(-1).query.limit, "100");
  passed.push("live continuity KPIs", "outside anomaly visible before acceptance", "100 row page bound");
  await dialog().getByRole("button", { name: "下一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.bm-board__table tbody tr code')?.textContent === '10.1.0.100');
  passed.push("server pagination beyond first page");
  await dialog().getByRole("checkbox", { name: "仅预期", exact: true }).check();
  await dialog().getByRole("button", { name: "查看范围外异常", exact: true }).click();
  await waitRows(1);
  await dialog().getByText("接续业务内容不一致", { exact: true }).waitFor();
  assert.ok(requests.filter((r) => r.p.endsWith("/diffs")).at(-1).query.color === "red");
  await dialog().getByText("字段变化 1 项", { exact: true }).click();
  await dialog().getByText("aa:bb", { exact: true }).waitFor();
  passed.push("late outside anomaly found with server filter", "scope switch resets pagination", "field integrity evidence readable");
  await page.screenshot({ path: path.join(output, "cutover-monitor-board.png"), fullPage: true });
  await dialog().getByRole("combobox", { name: "判定筛选", exact: true }).selectOption("");
  await waitRows(100);
  // Slow response for one sheet must not overwrite the next sheet selection.
  delayedSheet = "s1";
  const delayedRequest = page.waitForRequest((r) => r.url().includes("sheet_id=s1"));
  await dialog().locator(".bs-cmp-nav").getByRole("tab", { name: /ARP · 公网/ }).click();
  await delayedRequest;
  await dialog().locator(".bs-cmp-nav").getByRole("tab", { name: /ARP · 专线/ }).click();
  await page.waitForTimeout(1300);
  assert.ok((await table().locator("tbody tr td:first-child").allTextContents()).every((v) => v.includes("专线")));
  passed.push("slow sheet response cannot overwrite current sheet");
  delayedSheet = "";
  await dialog().locator(".bs-cmp-nav").getByRole("tab", { name: "全部监控项", exact: true }).click();
  uncertain = true;
  await dialog().getByRole("button", { name: "刷新看板", exact: true }).click();
  await dialog().getByText("数据待确认，不能视为验收通过", { exact: true }).waitFor();
  await dialog().getByText(/两侧采集间隔过大/).first().waitFor();
  passed.push("uncertain sampling visibly blocks healthy appearance", "duplicate key warning");
  uncertain = false;
  await dialog().getByRole("combobox", { name: "判定历史", exact: true }).selectOption("hist");
  await dialog().getByText("历史快照", { exact: true }).waitFor();
  const historyBoardRequests = requests.filter((r) => r.p.endsWith("/board")).length;
  await page.waitForTimeout(10500);
  assert.equal(requests.filter((r) => r.p.endsWith("/board")).length, historyBoardRequests);
  passed.push("historical view remains frozen");
  liveRunId = "live-2";
  await dialog().getByRole("combobox", { name: "判定历史", exact: true }).selectOption("");
  await waitRows(100);
  const liveCount = requests.filter((r) => r.p.endsWith("/board")).length;
  await page.waitForTimeout(10500);
  assert.ok(requests.filter((r) => r.p.endsWith("/board")).length > liveCount);
  passed.push("active live board automatically refreshes", "refresh cycles do not overlap");
  failBoard = true;
  await dialog().getByRole("button", { name: "刷新看板", exact: true }).click();
  await dialog().getByRole("alert").filter({ hasText: "获取失败" }).waitFor();
  assert.equal(await table().locator("tbody tr").count(), 100);
  passed.push("refresh failure keeps snapshot with explicit error");
  failBoard = false;
  await page.setViewportSize({ width: 390, height: 1000 });
  assert.ok(await dialog().locator(".bm-live-kpis").evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
  assert.ok(await dialog().locator(".bm-board").evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
  await page.screenshot({ path: path.join(output, "cutover-monitor-mobile.png"), fullPage: true });
  passed.push("390px responsive board", "table overflow stays within scroll container");
  await dialog().getByRole("button", { name: "割接批次", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.bm-expect-table tbody tr').length === 100);
  await dialog().getByRole("checkbox", { name: "ARP · 公网 业务 0", exact: true }).check();
  assert.ok(await dialog().getByRole("checkbox", { name: "ARP · 公网 业务 0", exact: true }).isChecked());
  await dialog().getByRole("button", { name: "下一页", exact: true }).click();
  await dialog().getByRole("checkbox", { name: "ARP · 公网 业务 100", exact: true }).check();
  await dialog().locator(".ct-editor__nav").getByRole("tab", { name: /ARP · 专线/ }).click();
  assert.equal(await dialog().getByRole("checkbox", { name: "ARP · 专线 业务 0", exact: true }).isChecked(), false);
  await dialog().getByRole("button", { name: "新建", exact: true }).click();
  const create = writes.at(-1);
  assert.deepEqual(create.expect_set.items, [{ metric_id: "arp", sheet_id: "s1", key: ["biz-0"] }, { metric_id: "arp", sheet_id: "s1", key: ["biz-100"] }]);
  passed.push("scope picker renders at most 100 rows", "checkbox click toggles exactly once", "selection survives pagination", "same business key in another sheet stays unselected", "wave stores structured keys and precise sheet scope");
  await dialog().getByRole("button", { name: "关闭", exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.goto(`${base}/network/cutover/monitor-templates`);
  await page.locator("table tbody tr").filter({ hasText: monitor.name }).getByRole("button", { name: "编辑", exact: true }).click();
  await dialog().getByText("范围外业务持续保护", { exact: true }).waitFor();
  await dialog().getByRole("button", { name: /保存/, exact: true }).click();
  await dialog().waitFor({ state: "hidden" });
  assert.equal(writes.at(-1).defaults.custom_setting, false);
  assert.equal(writes.at(-1).defaults.threshold, 7);
  assert.equal(writes.at(-1).defaults.out_of_expect, "strict");
  assert.equal(writes.at(-1).sheet_overrides[0].success[0].new_groups[0][0].value, "added");
  assert.equal(writes.at(-1).sheet_overrides[0].success[0].name, "客户迁移规则");
  passed.push("monitor edit preserves extended defaults", "outside protection policy explained", "legacy group tokens and rule metadata preserved");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed, errors }, null, 2));
} catch (error) {
  await page.screenshot({ path: path.join(output, "cutover-monitor-failure.png"), fullPage: true });
  console.log(JSON.stringify({ passed, errors, recentRequests: requests.slice(-12) }, null, 2));
  throw error;
} finally {
  await browser.close();
}
