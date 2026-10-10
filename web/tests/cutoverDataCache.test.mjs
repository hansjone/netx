import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { cutoverCachedGet, cutoverCachedGetSWR, invalidateCutoverCache } from "../src/pages/network/cutoverDataCache.ts";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};

beforeEach(() => invalidateCutoverCache());

test("concurrent readers share one fetch", async () => {
  const pending = deferred();
  let calls = 0;
  const fetcher = () => { calls++; return pending.promise; };
  const first = cutoverCachedGet("batches", fetcher);
  const second = cutoverCachedGet("batches", fetcher);
  pending.resolve("fresh");
  assert.deepEqual(await Promise.all([first, second]), ["fresh", "fresh"]);
  assert.equal(calls, 1);
});

test("invalidated pending requests cannot repopulate the cache", async () => {
  const pending = deferred();
  const old = cutoverCachedGet("batches", () => pending.promise);
  invalidateCutoverCache("batches");
  pending.resolve("stale");
  await old;
  assert.equal(await cutoverCachedGet("batches", async () => "fresh"), "fresh");
});

test("forced refresh supersedes a slow older request", async () => {
  const pending = deferred();
  const old = cutoverCachedGet("lists", () => pending.promise);
  assert.equal(await cutoverCachedGet("lists", async () => "fresh", { force: true }), "fresh");
  pending.resolve("old");
  await old;
  assert.equal(await cutoverCachedGet("lists", async () => "unexpected"), "fresh");
});

test("a failed fetch can be retried", async () => {
  await assert.rejects(cutoverCachedGet("lists", async () => { throw new Error("offline"); }));
  assert.equal(await cutoverCachedGet("lists", async () => "recovered"), "recovered");
});

test("SWR readers share background refresh and each receives the fresh value", async () => {
  await cutoverCachedGet("lists", async () => "cached");
  const pending = deferred();
  let calls = 0;
  const notifications = [];
  const fetcher = () => { calls++; return pending.promise; };
  const first = await cutoverCachedGetSWR("lists", fetcher, (x) => notifications.push(x));
  const second = await cutoverCachedGetSWR("lists", fetcher, (x) => notifications.push(x));
  assert.equal(first, "cached");
  assert.equal(second, "cached");
  pending.resolve("fresh");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.deepEqual(notifications, ["fresh", "fresh"]);
});

test("invalidated SWR requests do not notify with stale data", async () => {
  await cutoverCachedGet("lists", async () => "cached");
  const pending = deferred();
  const notifications = [];
  await cutoverCachedGetSWR("lists", () => pending.promise, (x) => notifications.push(x));
  invalidateCutoverCache("lists");
  pending.resolve("stale");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(notifications, []);
});
