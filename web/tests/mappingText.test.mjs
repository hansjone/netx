import assert from "node:assert/strict";
import { test } from "node:test";
import { parseMapText, mapTextDirty } from "../src/features/cutover/lib/mappingText.ts";

test("comma, pipe, tab and exactly-two-token space lines are all accepted", () => {
  const r = parseMapText("a1,b1\na2|b2\na3\tb3\na4 b4\n# note\n\nbefore_if,after_if");
  assert.deepEqual(r.rows.map((x) => [x.before_if, x.after_if]), [["a1", "b1"], ["a2", "b2"], ["a3", "b3"], ["a4", "b4"]]);
  assert.deepEqual(r.invalidLines, []);
});

test("unreadable lines are reported with their line numbers instead of dropped", () => {
  const r = parseMapText("a1,b1\nonlyone\na,b,c\na5 b5 c5\nok1,ok2");
  assert.deepEqual(r.invalidLines, [2, 3, 4]);
  assert.equal(r.rows.length, 2);
});

test("repeated before port is flagged and the first pair is kept", () => {
  const r = parseMapText("a,b\na,c");
  assert.deepEqual(r.duplicateBefore, ["a"]);
  assert.deepEqual(r.rows, [{ before_if: "a", after_if: "b" }]);
});

test("dirty check ignores order and spacing but sees edits and invalid text", () => {
  const saved = [{ before_if: "a", after_if: "b" }, { before_if: "c", after_if: "d" }];
  assert.equal(mapTextDirty("c , d\na,b", saved), false);
  assert.equal(mapTextDirty("a,b", saved), true);
  assert.equal(mapTextDirty("a,b\nc,d\nbroken", saved), true);
  assert.equal(mapTextDirty("", undefined), false);
  assert.equal(mapTextDirty("x,y", undefined), true);
});
