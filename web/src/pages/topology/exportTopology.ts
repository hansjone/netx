import { getStraightPath } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";
import type { TopologyViewGraph, TopologyViewItem, TopologyViewNodeItem } from "../../types";
import type { NeNodeData } from "./TopologyReactFlowView";
import { isAggregateEdgeId, type LinkEdgeData } from "./linkDisplay";
import {
  TOPO_HANDLE_X,
  TOPO_HANDLE_Y,
  TOPO_ICON,
  TOPO_NODE_W,
  TOPO_REGION_HANDLE_X,
  TOPO_REGION_HANDLE_Y,
  TOPO_REGION_ICON_H,
} from "./topoGeometry";
import type { VendorColors } from "./displayPrefs";
import { vendorColorForNode } from "./vendorTone";

const NE_ICON_URL = "/topo/ne-router.png";
const REGION_ICON_URL = "/topo/region-building.png";

export type TopologyVectorSvgTheme = {
  labelColor: string;
  edgeLabelColor: string;
  vendorColors: VendorColors;
  hideIp: boolean;
  hideVendor: boolean;
};

type IconAssets = {
  neTinted: Map<string, string>;
  regionPlain: string;
};

function sanitizeFilename(name: string): string {
  const trimmed = String(name || "topology")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
    .replace(/\s+/g, "_")
    .slice(0, 80);
  return trimmed || "topology";
}

function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function downloadText(filename: string, text: string, mime: string): void {
  downloadBlob(filename, new Blob([text], { type: mime }));
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load icon: ${url}`));
    img.src = url;
  });
}

/** Match canvas: `.topo-node__icon-art` + masked `.topo-node__icon-tint` (mix-blend-mode: color). */
async function tintedIconDataUrl(
  img: HTMLImageElement,
  color: string,
  w: number,
  h: number,
): Promise<string> {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas unsupported");

  ctx.drawImage(img, 0, 0, w, h);

  const tint = document.createElement("canvas");
  tint.width = w;
  tint.height = h;
  const tctx = tint.getContext("2d");
  if (!tctx) throw new Error("canvas unsupported");
  tctx.fillStyle = color;
  tctx.fillRect(0, 0, w, h);
  tctx.globalCompositeOperation = "destination-in";
  tctx.drawImage(img, 0, 0, w, h);

  ctx.globalCompositeOperation = "color";
  ctx.drawImage(tint, 0, 0);

  return canvas.toDataURL("image/png");
}

async function plainIconDataUrl(img: HTMLImageElement, w: number, h: number): Promise<string> {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas unsupported");
  ctx.drawImage(img, 0, 0, w, h);
  return canvas.toDataURL("image/png");
}

async function prepareIconAssets(
  nodes: Node<NeNodeData>[],
  theme: TopologyVectorSvgTheme,
): Promise<IconAssets> {
  const [neImg, regionImg] = await Promise.all([loadImage(NE_ICON_URL), loadImage(REGION_ICON_URL)]);
  const colors = new Set<string>();
  for (const n of nodes) {
    if (!isRegionNode(n)) colors.add(vendorColor(theme, n.data));
  }
  const neTinted = new Map<string, string>();
  await Promise.all(
    [...colors].map(async (color) => {
      neTinted.set(color, await tintedIconDataUrl(neImg, color, TOPO_ICON, TOPO_ICON));
    }),
  );
  const regionPlain = await plainIconDataUrl(regionImg, TOPO_ICON, TOPO_REGION_ICON_H);
  return { neTinted, regionPlain };
}

function isRegionNode(node: Node<NeNodeData>): boolean {
  return node.data.kind === "region" || String(node.id || "").startsWith("region:");
}

function nodeApiPosition(node: Node<NeNodeData>): { x: number; y: number } {
  const isRegion = isRegionNode(node);
  const hx = isRegion ? TOPO_REGION_HANDLE_X : TOPO_HANDLE_X;
  const hy = isRegion ? TOPO_REGION_HANDLE_Y : TOPO_HANDLE_Y;
  return { x: node.position.x + hx, y: node.position.y + hy };
}

function nodeGlyphBox(node: Node<NeNodeData>): { x: number; y: number; w: number; h: number } {
  const isRegion = isRegionNode(node);
  const w = TOPO_ICON;
  const h = isRegion ? TOPO_REGION_ICON_H : TOPO_ICON;
  return {
    x: node.position.x + (TOPO_NODE_W - w) / 2,
    y: node.position.y,
    w,
    h,
  };
}

function nodeHandleCenter(node: Node<NeNodeData>): { x: number; y: number } {
  const box = nodeGlyphBox(node);
  return { x: box.x + box.w / 2, y: box.y + box.h / 2 };
}

function vendorColor(theme: TopologyVectorSvgTheme, data: NeNodeData): string {
  return vendorColorForNode(data, theme.vendorColors);
}

function xmlEscape(value: string): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function xmlAttr(name: string, value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  return ` ${name}="${xmlEscape(String(value))}"`;
}

type Bounds = { minX: number; minY: number; maxX: number; maxY: number };

function growBounds(bounds: Bounds, x: number, y: number, pad = 0): void {
  bounds.minX = Math.min(bounds.minX, x - pad);
  bounds.minY = Math.min(bounds.minY, y - pad);
  bounds.maxX = Math.max(bounds.maxX, x + pad);
  bounds.maxY = Math.max(bounds.maxY, y + pad);
}

function nodeCaptionLines(node: Node<NeNodeData>, theme: TopologyVectorSvgTheme): string[] {
  const data = node.data;
  const isRegion = isRegionNode(node);
  const name = data.label || (!theme.hideIp ? data.ne_ip : "") || (isRegion ? "Region" : "NE");
  const lines = [name];
  if (!isRegion) {
    const meta = [
      theme.hideIp || !data.ne_ip || data.ne_ip === name ? "" : data.ne_ip,
      theme.hideVendor || !data.vendor ? "" : data.vendor,
    ].filter(Boolean);
    if (meta.length) lines.push(meta.join(" / "));
  }
  return lines;
}

function computeGraphBounds(
  nodes: Node<NeNodeData>[],
  displayEdges: Edge[],
  centers: Map<string, { x: number; y: number }>,
  theme: TopologyVectorSvgTheme,
): Bounds {
  const bounds: Bounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const node of nodes) {
    const box = nodeGlyphBox(node);
    growBounds(bounds, box.x, box.y);
    growBounds(bounds, box.x + box.w, box.y + box.h);
    const lines = nodeCaptionLines(node, theme);
    const captionY = box.y + box.h + 1;
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const approxWidth = Math.max(24, line.length * (i === 0 ? 5 : 4.2));
      const lineY = captionY + i * (i === 0 ? 10 : 8);
      growBounds(bounds, box.x + box.w / 2 - approxWidth / 2, lineY);
      growBounds(bounds, box.x + box.w / 2 + approxWidth / 2, lineY + (i === 0 ? 10 : 8));
    }
  }
  for (const edge of displayEdges) {
    const a = centers.get(edge.source);
    const b = centers.get(edge.target);
    if (!a || !b) continue;
    growBounds(bounds, a.x, a.y);
    growBounds(bounds, b.x, b.y);
    const label = String(edge.label || "").trim();
    if (label) {
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      growBounds(bounds, mx, my, Math.max(20, label.length * 3));
    }
  }
  if (!Number.isFinite(bounds.minX)) {
    return { minX: 0, minY: 0, maxX: 800, maxY: 600 };
  }
  return bounds;
}

function edgeVisual(edge: Edge): { stroke: string; strokeWidth: number; strokeDasharray?: string } {
  const style = (edge.style || {}) as {
    stroke?: string;
    strokeWidth?: number;
    strokeDasharray?: string;
  };
  return {
    stroke: String(style.stroke || "#64748b"),
    strokeWidth: Number(style.strokeWidth || 2),
    strokeDasharray: style.strokeDasharray ? String(style.strokeDasharray) : undefined,
  };
}

function renderNodeIcon(
  node: Node<NeNodeData>,
  theme: TopologyVectorSvgTheme,
  icons: IconAssets,
): string {
  const box = nodeGlyphBox(node);
  if (isRegionNode(node)) {
    return `<image href="${icons.regionPlain}" x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" preserveAspectRatio="xMidYMid meet"/>`;
  }
  const color = vendorColor(theme, node.data);
  const href = icons.neTinted.get(color);
  if (!href) return "";
  return `<image href="${href}" x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" preserveAspectRatio="xMidYMid meet"/>`;
}

function renderNode(node: Node<NeNodeData>, theme: TopologyVectorSvgTheme, icons: IconAssets): string {
  const box = nodeGlyphBox(node);
  const centerX = box.x + box.w / 2;
  const icon = renderNodeIcon(node, theme, icons);
  const lines = nodeCaptionLines(node, theme);
  const captionParts = lines.map((line, idx) => {
    const y = box.y + box.h + 1 + (idx === 0 ? 8 : 8 + idx * 8);
    const fontSize = idx === 0 ? 8 : 7;
    const opacity = idx === 0 ? 1 : 0.72;
    return `<text x="${centerX}" y="${y}" text-anchor="middle" font-family="Segoe UI, system-ui, sans-serif" font-size="${fontSize}" font-weight="${idx === 0 ? 600 : 400}" fill="${xmlEscape(theme.labelColor)}" opacity="${opacity}">${xmlEscape(line)}</text>`;
  });
  return [
    `<g id="node-${xmlEscape(node.id)}" class="topo-export-node">`,
    icon,
    ...captionParts,
    `</g>`,
  ].join("");
}

function renderEdge(
  edge: Edge,
  centers: Map<string, { x: number; y: number }>,
  theme: TopologyVectorSvgTheme,
): string {
  const a = centers.get(edge.source);
  const b = centers.get(edge.target);
  if (!a || !b) return "";
  const data = (edge.data || {}) as LinkEdgeData;
  const index = Number(data.parallelIndex || 0);
  const count = Math.max(1, Number(data.parallelCount || 1));
  const offset = (index - (count - 1) / 2) * 10;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ox = (-dy / len) * offset;
  const oy = (dx / len) * offset;
  const sx = a.x + ox;
  const sy = a.y + oy;
  const tx = b.x + ox;
  const ty = b.y + oy;
  const visual = edgeVisual(edge);
  const dash = visual.strokeDasharray
    ? ` stroke-dasharray="${xmlEscape(visual.strokeDasharray)}"`
    : "";
  const [path, labelX, labelY] = getStraightPath({ sourceX: sx, sourceY: sy, targetX: tx, targetY: ty });
  const label = String(edge.label || "").trim();
  const labelMarkup = label
    ? `<text x="${labelX}" y="${labelY}" text-anchor="middle" dominant-baseline="middle" font-family="ui-monospace, SFMono-Regular, Consolas, monospace" font-size="7" fill="${xmlEscape(theme.edgeLabelColor)}">${xmlEscape(label)}</text>`
    : "";
  return [
    `<g id="edge-${xmlEscape(edge.id)}" class="topo-export-edge">`,
    `<path d="${xmlEscape(path)}" fill="none" stroke="${xmlEscape(visual.stroke)}" stroke-width="${visual.strokeWidth}" stroke-linecap="round"${dash}/>`,
    labelMarkup,
    `</g>`,
  ].join("");
}

/** Build a vector SVG using the same PNG icons + vendor tint as the canvas. */
export async function buildTopologyVectorSvg(
  nodes: Node<NeNodeData>[],
  displayEdges: Edge[],
  theme: TopologyVectorSvgTheme,
  title?: string,
): Promise<string> {
  const icons = await prepareIconAssets(nodes, theme);
  const centers = new Map(nodes.map((n) => [n.id, nodeHandleCenter(n)] as const));
  const bounds = computeGraphBounds(nodes, displayEdges, centers, theme);
  const pad = 48;
  const minX = bounds.minX - pad;
  const minY = bounds.minY - pad;
  const width = bounds.maxX - bounds.minX + pad * 2;
  const height = bounds.maxY - bounds.minY + pad * 2;
  const edgeMarkup = displayEdges.map((e) => renderEdge(e, centers, theme)).join("");
  const nodeMarkup = nodes.map((n) => renderNode(n, theme, icons)).join("");
  const titleMarkup = title ? `<title>${xmlEscape(title)}</title>` : "";
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="${minX} ${minY} ${width} ${height}" width="${Math.round(width)}" height="${Math.round(height)}">`,
    titleMarkup,
    `<desc>netx topology vector export</desc>`,
    `<g id="edges">${edgeMarkup}</g>`,
    `<g id="nodes">${nodeMarkup}</g>`,
    `</svg>`,
  ].join("\n");
}

export function buildExportGraph(
  view: TopologyViewItem,
  nodes: Node<NeNodeData>[],
  edges: Edge[],
  serverGraph?: TopologyViewGraph | null,
): TopologyViewGraph {
  const serverNodeMap = new Map(
    (serverGraph?.nodes || []).map((n) => [n.fabric_node_id, n] as const),
  );
  const serverEdgeMap = new Map((serverGraph?.edges || []).map((e) => [e.id, e] as const));

  const exportNodes: TopologyViewNodeItem[] = nodes.map((n) => {
    const pos = nodeApiPosition(n);
    const server = serverNodeMap.get(n.id);
    return {
      fabric_node_id: n.id,
      managed_ne_id: n.data.managed_ne_id || server?.managed_ne_id || "",
      ume_ne_id: n.data.ume_ne_id || server?.ume_ne_id || "",
      label: n.data.label || server?.label || "",
      x: pos.x,
      y: pos.y,
      locked: server?.locked || false,
      name: server?.name || n.data.label || "",
      ip: n.data.ne_ip || server?.ip || "",
      vendor: n.data.vendor || server?.vendor || "",
      device_type: server?.device_type || (isRegionNode(n) ? "region" : ""),
      connect_status: n.data.connect_status || server?.connect_status || "",
      managed_source: n.data.managed_source || server?.managed_source,
      kind: n.data.kind || server?.kind,
      folder_id: n.data.folder_id || server?.folder_id,
      view_id: n.data.view_id || server?.view_id,
      node_count: n.data.node_count ?? server?.node_count,
    };
  });

  const physicalEdges = edges.filter((e) => !isAggregateEdgeId(String(e.id)));
  const exportEdges = physicalEdges.map((e) => {
    const data = (e.data || {}) as LinkEdgeData;
    const server = serverEdgeMap.get(e.id);
    return {
      id: e.id,
      a_node_id: e.source,
      b_node_id: e.target,
      a_port: data.source_port || server?.a_port || "",
      b_port: data.target_port || server?.b_port || "",
      source: data.source || server?.source || "manual",
      status: server?.status || "active",
      layer: server?.layer || "physical",
      stroke_color: data.stroke_color || server?.stroke_color,
      stroke_width: data.stroke_width || server?.stroke_width,
      line_style: data.line_style || server?.line_style,
      discovered_at: data.discovered_at ?? server?.discovered_at ?? null,
      display_label: data.display_label || server?.display_label,
    };
  });

  return {
    view,
    nodes: exportNodes,
    edges: exportEdges,
    truncated: serverGraph?.truncated,
    truncate_reason: serverGraph?.truncate_reason,
    outside_peers: serverGraph?.outside_peers,
    world_transform: serverGraph?.world_transform,
    scatter: serverGraph?.scatter,
  };
}

export function topologyGraphToXml(graph: TopologyViewGraph): string {
  const view = graph.view;
  const lines: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<topology xmlns="urn:netx:topology:export" version="1"${xmlAttr("exported-at", new Date().toISOString())}>`,
    "  <view",
    `${xmlAttr("id", view.id)}${xmlAttr("name", view.name)}${xmlAttr("kind", view.kind)}${xmlAttr("folder_id", view.folder_id)}${xmlAttr("remark", view.remark || "")} />`,
    `  <nodes count="${graph.nodes.length}">`,
  ];

  for (const n of graph.nodes) {
    lines.push(
      `    <node${xmlAttr("id", n.fabric_node_id)}${xmlAttr("x", n.x)}${xmlAttr("y", n.y)}${xmlAttr("label", n.label)}${xmlAttr("name", n.name)}${xmlAttr("ip", n.ip)}${xmlAttr("vendor", n.vendor)}${xmlAttr("kind", n.kind)}${xmlAttr("managed_ne_id", n.managed_ne_id)}${xmlAttr("ume_ne_id", n.ume_ne_id)}${xmlAttr("folder_id", n.folder_id)}${xmlAttr("view_id", n.view_id)} />`,
    );
  }
  lines.push("  </nodes>");
  lines.push(`  <edges count="${graph.edges.length}">`);

  for (const e of graph.edges) {
    lines.push(
      `    <edge${xmlAttr("id", e.id)}${xmlAttr("a_node_id", e.a_node_id)}${xmlAttr("b_node_id", e.b_node_id)}${xmlAttr("a_port", e.a_port)}${xmlAttr("b_port", e.b_port)}${xmlAttr("source", e.source)}${xmlAttr("status", e.status)}${xmlAttr("layer", e.layer)}${xmlAttr("display_label", e.display_label || "")} />`,
    );
  }
  lines.push("  </edges>");
  lines.push("</topology>");
  return lines.join("\n");
}

export async function exportTopologySvg(
  nodes: Node<NeNodeData>[],
  displayEdges: Edge[],
  filenameBase: string,
  theme: TopologyVectorSvgTheme,
  title?: string,
): Promise<void> {
  const svg = await buildTopologyVectorSvg(nodes, displayEdges, theme, title);
  downloadText(`${sanitizeFilename(filenameBase)}.svg`, svg, "image/svg+xml;charset=utf-8");
}

export function exportTopologyXml(graph: TopologyViewGraph, filenameBase: string): void {
  const xml = topologyGraphToXml(graph);
  downloadText(`${sanitizeFilename(filenameBase)}.xml`, xml, "application/xml;charset=utf-8");
}
