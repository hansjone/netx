// Fixture-only browser regression: never contacts a device or real database.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.NETX_PLAYWRIGHT_MODULE || "playwright");
const output = path.resolve(process.env.NETX_TEST_OUTPUT || "../docs/reviews/assets");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.NETX_TEST_BROWSER || "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const base = process.env.NETX_TEST_URL || "http://127.0.0.1:5179";
let active = false;
let failTasks = false;
let failTask = false;
let failSheet = false;
let slowPurpose = false;
let listDelay = 0;
let inFlight = 0;
let maxInFlight = 0;
let counts = { lists: 0, detail: 0, progress: 0, profiles: 0 };
const metricRequests = [];
let lastDownload = "";
let task = { id: "t", source: "managed", ne_name: "PE-FAST", ne_ip: "10.10.0.1", vendor: "ZTE",
  device_type: "ZXROS", status: "paused", collect_running: false, last_error: "", interval_sec: 3600,
  retention_days: 30, daily_keep_enabled: false, daily_keep_count: 10,
  items: [{ id: "i-bgp", source_profile_id: "zte.bgp", enabled: true, bindings: [] },
    { id: "i-arp", source_profile_id: "zte.arp", enabled: true, bindings: [] }] };
const profiles = [
  { profile_id: "zte.bgp", title: "BGP 邻居", metric_id: "bgp_peer", kind: "collect", collect_lane: "light", command_template: "show bgp summary", placeholders: [] },
  { profile_id: "zte.arp", title: "ARP 条目", metric_id: "arp", kind: "collect", collect_lane: "heavy", command_template: "show arp", placeholders: [] },
];
const rows = Array.from({ length: 120 }, (_, n) => ({ neighbor: `10.2.1.${n}`, vrf: "core", state: n % 10 ? "Established" : "Idle" }));
const batch = (id, status = "success") => ({ id, status, row_count: 120, command_count: 2, message: "",
  started_at: "2026-10-10T15:00:00Z", ended_at: status === "running" ? null : "2026-10-10T15:00:10Z",
  protected: status === "running" || id === "b", is_baseline: id === "b", alias: id === "b" ? "割接基准" : "" });
const batches = [batch("b"), batch("partial", "partial"), batch("active", "running"), batch("slow-batch"),
  ...Array.from({ length: 50 }, (_, n) => batch(`history-${n}`))];
const commands = [
  { id: "c1", raw_command: "show bgp summary", metric_id: "bgp_peer", profile_id: "zte.bgp", has_raw: true, parse_status: "ok", row_count: 120, raw_line_count: 124 },
  { id: "cslow", raw_command: "show arp", metric_id: "arp", profile_id: "zte.arp", has_raw: true, parse_status: "ok", row_count: 1, raw_line_count: 4 },
];
const workbook = (id) => ({ ...batch(id), task_id: "t", commands, sheet_count: 2, sheets_with_data: 2,
  sheets: [{ metric_id: "bgp_peer", title: "BGP 邻居", row_count: 120, commands: [commands[0]] },
    { metric_id: "arp", title: "ARP 条目", row_count: 1, commands: [commands[1]] }] });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
await page.route("**/v1/**", async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const p = url.pathname;
  let data = {};
  if (p === "/v1/auth/me") data = { user: { id: "test", username: "test", role: "admin", is_active: true } };
  else if (p === "/v1/biz-state/tasks") {
    counts.lists++;
    maxInFlight = Math.max(maxInFlight, ++inFlight);
    try {
      const purpose = url.searchParams.get("purpose") || "";
      if (slowPurpose && purpose === "portrait") await pause(900);
      if (listDelay) await pause(listDelay);
      if (failTasks) { await route.fulfill({ status: 503, json: { detail: "fixture: tasks unavailable" } }); return; }
      const all = [{ ...task, collect_running: active }, { ...task, id: "slow", ne_name: "PE-SLOW", purpose: "cutover_hf" },
        ...Array.from({ length: 118 }, (_, n) => ({ ...task, id: `extra-${n}`, ne_name: `PE-${n}`, purpose: "portrait" }))];
      data = { items: purpose ? all.filter((row) => purpose === "portrait" ? row.purpose !== "cutover_hf" : row.purpose === purpose) : all };
    } finally { inFlight--; }
  } else if (p === "/v1/biz-state/tasks/t" || p === "/v1/biz-state/tasks/slow") {
    counts.detail++;
    if (p.endsWith("/slow")) await pause(900);
    if (failTask) { await route.fulfill({ status: 503, json: { detail: "fixture: config unavailable" } }); return; }
    if (req.method() === "PATCH") task = { ...task, ...req.postDataJSON() };
    data = p.endsWith("/slow") ? { ...task, id: "slow", ne_name: "PE-SLOW" } : { ...task, collect_running: active };
  } else if (p.endsWith("/progress")) { counts.progress++; data = { ...task, collect_running: active }; delete data.items; }
  else if (p === "/v1/biz-state/profiles") { counts.profiles++; data = { items: profiles }; }
  else if (p.endsWith("/batches") && p.includes("/tasks/")) data = { items: batches.slice(0, Number(url.searchParams.get("limit") || 50)) };
  else if (p.includes("/metrics/")) {
    metricRequests.push(Object.fromEntries(url.searchParams));
    if (p.endsWith("/arp")) await pause(900);
    if (failSheet) { await route.fulfill({ status: 503, json: { detail: "fixture: metric unavailable" } }); return; }
    const metric = p.split("/").at(-1);
    const kw = url.searchParams.get("kw") || "";
    const col = url.searchParams.get("column") || "";
    const all = metric === "arp" ? [{ ip: "ARP_ONLY", interface: "gei-1/1", mac: "aa:bb" }] : rows;
    const filtered = all.filter((row) => JSON.stringify(col ? row[col] : row).toLowerCase().includes(kw.toLowerCase()));
    const pageN = Number(url.searchParams.get("page") || 1);
    const size = Number(url.searchParams.get("page_size") || 50);
    data = { items: filtered.slice((pageN - 1) * size, pageN * size), total: filtered.length, page: pageN,
      columns: Object.keys(all[0]).map((key) => ({ key, header: key })) };
  } else if (p.endsWith("/raw.txt")) {
    lastDownload = p;
    await route.fulfill({ status: 200, contentType: "text/plain", headers: { "Content-Disposition": 'attachment; filename="raw.txt"' }, body: "FIXTURE RAW" }); return;
  } else if (p.includes("/commands/")) {
    if (p.endsWith("/cslow")) await pause(900);
    data = { ...commands.find((c) => c.id === p.split("/").at(-1)), batch_id: "b", raw_text: p.endsWith("/cslow") ? "OLD_ARP_LOG" : "CURRENT_BGP_LOG" };
  } else if (p.startsWith("/v1/biz-state/batches/")) {
    const id = p.split("/").at(-1);
    if (id === "slow-batch") await pause(900);
    data = workbook(id);
  } else data = { items: [] };
  await route.fulfill({ status: 200, json: data });
});

const main = page.locator(".bs-monitor-workspace");
const taskPanel = page.locator(".bs-monitor-task");
const book = page.locator(".bs-workbook-modal");
const openTask = async (name = "PE-FAST") => {
  await main.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "详情", exact: true }).click();
};
const historyRow = (id) => taskPanel.locator(".bs-monitor-batches tbody tr").filter({ has: page.getByRole("checkbox", { name: id === "b" ? "割接基准" : id, exact: true }) });
const passed = [];
try {
  await page.goto(`${base}/network/cutover/biz-state`);
  await main.getByRole("row").filter({ hasText: "PE-FAST" }).waitFor();
  assert.equal(await main.locator("tbody tr").count(), 50);
  await main.getByRole("button", { name: "下一页", exact: true }).click();
  assert.equal(await main.locator("tbody tr").count(), 50);
  await main.getByRole("button", { name: "上一页", exact: true }).click();
  passed.push("bounded task rendering", "task pagination");
  await page.screenshot({ path: path.join(output, "biz-state-tasks.png"), fullPage: true });
  slowPurpose = true;
  await main.getByLabel("任务用途", { exact: true }).selectOption("portrait");
  await main.getByLabel("任务用途", { exact: true }).selectOption("cutover_hf");
  await main.getByRole("row").filter({ hasText: "PE-SLOW" }).waitFor();
  await pause(1100);
  assert.equal(await main.locator("tbody tr").count(), 1);
  slowPurpose = false;
  await main.getByLabel("任务用途", { exact: true }).selectOption("all");
  await main.getByRole("row").filter({ hasText: "PE-FAST" }).waitFor();
  passed.push("stale purpose response ignored");
  failTasks = true;
  await main.getByRole("button", { name: "刷新", exact: true }).click();
  await main.getByRole("alert").waitFor();
  assert.equal(await main.locator("tbody tr").count(), 50);
  failTasks = false;
  await main.getByRole("button", { name: "重试", exact: true }).click();
  await main.getByRole("alert").waitFor({ state: "detached" });
  passed.push("list error retains rows", "list retry");
  await openTask("PE-SLOW");
  await taskPanel.getByText("正在读取任务配置…", { exact: true }).waitFor();
  assert.ok(await taskPanel.getByRole("button", { name: "立即采集", exact: true }).isDisabled());
  await taskPanel.getByRole("button", { name: "关闭", exact: true }).click();
  await openTask();
  await taskPanel.getByRole("tab", { name: "监控项", exact: true }).waitFor();
  await pause(1100);
  assert.equal(await taskPanel.locator(".bs-monitor-heading > span:last-child").textContent(), "PE-FAST");
  passed.push("pending config actions disabled", "stale task response ignored");
  await taskPanel.getByLabel("采集周期", { exact: true }).fill("9");
  await taskPanel.getByLabel("保留天数", { exact: true }).fill("20");
  const saved = page.waitForRequest((req) => req.method() === "PATCH" && req.url().endsWith("/tasks/t"));
  await taskPanel.getByRole("button", { name: "保存周期", exact: true }).click();
  const body = (await saved).postDataJSON();
  assert.equal(body.interval_sec, 32400);
  assert.equal(body.retention_days, 20);
  await taskPanel.getByLabel("采集周期", { exact: true }).waitFor();
  await taskPanel.locator(".bs-monitor-task-content").waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector(".bs-monitor-task-content")?.hasAttribute("inert"));
  const profilesBefore = counts.profiles;
  await taskPanel.locator(".bs-profiles-table input[type=checkbox]").first().click();
  await page.waitForFunction(() => !document.querySelector(".bs-monitor-task-content")?.hasAttribute("inert")
    && document.querySelector(".bs-profiles-table input[type=checkbox]")?.checked === false);
  assert.ok(!await taskPanel.locator(".bs-profiles-table input[type=checkbox]").first().isChecked());
  assert.equal(counts.profiles, profilesBefore);
  passed.push("schedule save payload", "profile toggle", "profile cache");
  await page.screenshot({ path: path.join(output, "biz-state-config.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 1000 });
  await pause(300);
  assert.ok(await taskPanel.locator(".bs-schedule-row").evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
  assert.ok(await taskPanel.locator(".modal__footer").evaluate((el) => [...el.querySelectorAll("button")].every((button) => {
    const bounds = button.getBoundingClientRect();
    return bounds.left >= 0 && bounds.right <= window.innerWidth;
  })), "configuration footer stays inside viewport");
  await page.screenshot({ path: path.join(output, "biz-state-config-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await taskPanel.getByRole("tab", { name: "监控项", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(await taskPanel.getByRole("tab", { name: "采集批次", exact: true }).getAttribute("aria-selected"), "true");
  assert.equal(await taskPanel.locator(".bs-monitor-batches tbody tr").count(), 54);
  assert.ok(await taskPanel.locator(".bs-monitor-batches tbody tr").first().getByRole("checkbox").isDisabled());
  assert.ok(await taskPanel.locator(".bs-monitor-batches tbody tr").nth(2).getByRole("checkbox").isDisabled());
  passed.push("390px configuration", "keyboard task tabs", "active and baseline batches protected");
  await page.screenshot({ path: path.join(output, "biz-state-batches.png"), fullPage: true });
  await historyRow("slow-batch").getByRole("button", { name: "详情", exact: true }).click();
  await page.locator(".bs-collect-detail-modal").getByRole("button", { name: "关闭", exact: true }).click();
  await pause(1100);
  assert.equal(await page.locator(".bs-collect-detail-modal").count(), 0);
  passed.push("closed collection detail stays closed");
  await historyRow("slow-batch").getByRole("button", { name: "查看", exact: true }).click();
  await book.getByRole("button", { name: "关闭", exact: true }).click();
  await historyRow("b").getByRole("button", { name: "查看", exact: true }).click();
  await book.locator(".bs-sheet-table tbody tr").first().waitFor();
  await pause(1100);
  assert.ok((await book.locator(".bs-id-row__value").textContent()) === "b");
  await book.getByRole("tab", { name: /ARP 条目/ }).click();
  await book.getByRole("tab", { name: /BGP 邻居/ }).click();
  await book.locator(".bs-sheet-table tbody tr").first().waitFor();
  await pause(1100);
  assert.equal(await book.getByText("ARP_ONLY", { exact: true }).count(), 0);
  passed.push("stale workbook response ignored", "stale metric response ignored");
  await book.getByRole("button", { name: "下一页", exact: true }).click();
  await book.getByRole("cell", { name: "10.2.1.50", exact: true }).waitFor();
  metricRequests.length = 0;
  await book.getByLabel("筛选当前表…", { exact: true }).fill("10.2.1.2");
  await book.getByRole("cell", { name: "10.2.1.2", exact: true }).waitFor();
  assert.ok(metricRequests.every((request) => request.page === "1"));
  passed.push("search resets page without stale-page request");
  failSheet = true;
  await book.getByLabel("筛选当前表…", { exact: true }).fill("故障");
  await book.getByRole("alert").waitFor();
  assert.equal(await book.locator(".bs-sheet-table tbody tr td").count(), 0);
  await page.screenshot({ path: path.join(output, "biz-state-error.png"), fullPage: true });
  failSheet = false;
  await book.getByRole("button", { name: "重试", exact: true }).click();
  await book.getByText("本表无数据", { exact: true }).waitFor();
  await book.getByRole("button", { name: "清除筛选", exact: true }).click();
  await book.getByRole("cell", { name: "10.2.1.0", exact: true }).waitFor();
  passed.push("sheet error clears rows", "sheet retry", "clear filters");
  await page.screenshot({ path: path.join(output, "biz-state-workbook.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 1000 });
  await pause(300);
  assert.ok(await book.locator(".bs-sheet-filter").evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
  assert.ok(await book.locator(".modal__footer").evaluate((el) => [...el.querySelectorAll("button")].every((button) => {
    const bounds = button.getBoundingClientRect();
    return bounds.left >= 0 && bounds.right <= window.innerWidth;
  })), "workbook footer stays inside viewport");
  await page.screenshot({ path: path.join(output, "biz-state-workbook-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  passed.push("390px workbook");
  await book.getByRole("tab", { name: /^命令/ }).click();
  await book.locator(".bs-commands-card").nth(1).getByRole("button", { name: "原始日志", exact: true }).click();
  const raw = page.locator(".bs-rawlog-modal");
  await raw.getByRole("button", { name: "关闭", exact: true }).click();
  await book.locator(".bs-commands-card").first().getByRole("button", { name: "原始日志", exact: true }).click();
  await raw.getByText("CURRENT_BGP_LOG", { exact: true }).waitFor();
  await pause(1100);
  assert.equal(await raw.getByText("OLD_ARP_LOG", { exact: true }).count(), 0);
  const download = page.waitForEvent("download");
  await raw.getByRole("button", { name: "导出为文本", exact: true }).click();
  await download;
  assert.equal(lastDownload, "/v1/biz-state/batches/b/commands/c1/raw.txt");
  passed.push("stale log response ignored", "log export source");
  await raw.getByRole("button", { name: "关闭", exact: true }).click();
  await book.getByRole("button", { name: "关闭", exact: true }).click();
  await taskPanel.getByRole("button", { name: "关闭", exact: true }).click();
  failTask = true;
  await openTask();
  await taskPanel.getByRole("alert").waitFor();
  failTask = false;
  await taskPanel.getByRole("button", { name: "重试", exact: true }).click();
  await taskPanel.getByRole("tab", { name: "监控项", exact: true }).waitFor();
  passed.push("task config retry");
  await taskPanel.getByRole("button", { name: "关闭", exact: true }).click();
  active = true;
  listDelay = 4500;
  await page.reload();
  await main.getByRole("row").filter({ hasText: "PE-FAST" }).waitFor();
  counts.detail = counts.progress = 0;
  maxInFlight = inFlight;
  await openTask();
  await taskPanel.getByLabel("采集周期", { exact: true }).fill("7");
  await pause(14500);
  assert.ok(maxInFlight <= 1, `overlapping task polls: ${maxInFlight}`);
  assert.equal(counts.detail, 1, "polling does not reload task items");
  assert.ok(counts.progress >= 1);
  assert.equal(await taskPanel.getByLabel("采集周期", { exact: true }).inputValue(), "7");
  await taskPanel.getByRole("tab", { name: "采集批次", exact: true }).click();
  assert.equal(await taskPanel.locator(".bs-monitor-batches tbody tr").count(), 54);
  assert.deepEqual(errors, []);
  passed.push("server-started collection polling", "non-overlapping slow polls", "lightweight progress", "unsaved config preserved", "history limit stays 200", "no runtime errors");
  console.log(JSON.stringify({ passed, counts, maxInFlight, output }));
} catch (error) {
  await page.screenshot({ path: path.join(output, "biz-state-test-failure.png"), fullPage: true });
  throw error;
} finally { await browser.close(); }
