import assert from "node:assert/strict";
import { test } from "node:test";
import { buildViewCommitBody, isRegionNodeId } from "../src/features/topology/lib/saveDiff.ts";

test("local add/remove nodes; region nodes stay out of add/remove lists", () => {
  const body = buildViewCommitBody({
    nodes: [
      { id: "fab-a" },
      { id: "fab-new" },
      { id: "region:x", data: { kind: "region" } },
    ],
    edges: [],
    serverNodeIds: ["fab-a", "fab-gone", "region:y"],
    positions: [
      { fabric_node_id: "fab-a", x: 1, y: 2 },
      { fabric_node_id: "fab-new", x: 3, y: 4 },
      { fabric_node_id: "region:x", x: 5, y: 6 },
    ],
    pendingCreates: [],
    pendingDeletes: [],
  });
  assert.deepEqual(body.node_adds, ["fab-new"]);
  assert.deepEqual(body.node_removes, ["fab-gone"]);
  assert.ok(!body.node_adds.some(isRegionNodeId));
  assert.ok(!body.node_removes.some(isRegionNodeId));
  assert.equal(body.positions.length, 3);
});

test("only changed label overrides appear in positions payload from caller", () => {
  const body = buildViewCommitBody({
    nodes: [{ id: "fab-a" }],
    edges: [],
    serverNodeIds: ["fab-a"],
    positions: [{ fabric_node_id: "fab-a", x: 1, y: 2, label: "CUSTOM" }],
    pendingCreates: [],
    pendingDeletes: [],
  });
  assert.equal(body.positions[0].label, "CUSTOM");
});

test("deleted then re-created edge is not in edge_deletes", () => {
  const body = buildViewCommitBody({
    nodes: [{ id: "a" }, { id: "b" }],
    edges: [
      {
        id: "local:1",
        source: "a",
        target: "b",
        data: { source_port: "gi0/1", target_port: "gi0/2" },
      },
    ],
    serverNodeIds: ["a", "b"],
    positions: [],
    pendingCreates: ["local:1"],
    pendingDeletes: ["edge-server-1", "local:1"],
  });
  assert.deepEqual(body.edge_deletes, ["edge-server-1"]);
  assert.equal(body.edge_creates.length, 1);
  assert.equal(body.edge_creates[0].client_id, "local:1");
  assert.equal(body.edge_creates[0].a_port, "gi0/1");
});

test("undo after delete removes edge from edge_deletes when not pending", () => {
  const body = buildViewCommitBody({
    nodes: [{ id: "a" }, { id: "b" }],
    edges: [{ id: "edge-server-1", source: "a", target: "b" }],
    serverNodeIds: ["a", "b"],
    positions: [],
    pendingCreates: [],
    pendingDeletes: [],
  });
  assert.deepEqual(body.edge_deletes, []);
  assert.deepEqual(body.edge_creates, []);
});

test("styled or port-edited edge that is then deleted only appears in edge_deletes", () => {
  const style = { stroke_color: "#ff0000", stroke_width: 2, line_style: "dashed" };
  const body = buildViewCommitBody({
    nodes: [{ id: "a" }, { id: "b" }],
    edges: [],
    serverNodeIds: ["a", "b"],
    positions: [],
    pendingCreates: [],
    pendingDeletes: ["edge-1"],
    pendingPortEdits: [["edge-1", { a_port: "gi0/1", b_port: "gi0/2" }]],
    pendingStyles: [["edge-1", style]],
  });
  assert.deepEqual(body.edge_deletes, ["edge-1"]);
  assert.deepEqual(body.edge_port_edits, []);
  assert.deepEqual(body.edge_styles, []);
});

test("style on a new local edge is sent under its client id; dropped once the edge is gone", () => {
  const style = { stroke_color: "#00ff00", stroke_width: 3, line_style: "" };
  const edges = [{ id: "local:9", source: "a", target: "b", data: {} }];
  const kept = buildViewCommitBody({
    nodes: [{ id: "a" }, { id: "b" }],
    edges,
    serverNodeIds: ["a", "b"],
    positions: [],
    pendingCreates: ["local:9"],
    pendingDeletes: [],
    pendingStyles: [["local:9", style]],
  });
  assert.deepEqual(kept.edge_styles.map((s) => s.fabric_edge_id), ["local:9"]);
  const dropped = buildViewCommitBody({
    nodes: [{ id: "a" }, { id: "b" }],
    edges: [],
    serverNodeIds: ["a", "b"],
    positions: [],
    pendingCreates: [],
    pendingDeletes: [],
    pendingStyles: [["local:9", style]],
  });
  assert.deepEqual(dropped.edge_styles, []);
});
