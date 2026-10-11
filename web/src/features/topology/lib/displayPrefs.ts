import type { CSSProperties } from "react";
import {
  AUTO_LAYOUT_DISCOVER_KEY,
  CANVAS_BG_KEY,
  DEFAULT_CANVAS_BG,
  LABEL_COLORS_KEY,
  LEGACY_CANVAS_BG,
  VENDOR_COLORS_KEY,
} from "./constants";

export const VENDOR_TONE_KEYS = [
  "cisco",
  "huawei",
  "zte",
  "juniper",
  "nokia",
  "ericsson",
  "h3c",
  "ruijie",
  "mikrotik",
  "gray",
] as const;

export type VendorToneKey = (typeof VENDOR_TONE_KEYS)[number];
export type VendorColors = Record<VendorToneKey, string>;
export type LabelColors = { name: string; edgeLabel: string };

export const DEFAULT_LABEL_COLORS: LabelColors = {
  name: "#f1f5f9",
  edgeLabel: "#e2e8f0",
};

export const DEFAULT_VENDOR_COLORS: VendorColors = {
  cisco: "#049fd9",
  huawei: "#cf0a2c",
  zte: "#0091da",
  juniper: "#84b135",
  nokia: "#124191",
  ericsson: "#1e3a5f",
  h3c: "#7ac143",
  ruijie: "#7c3aed",
  mikrotik: "#ea580c",
  gray: "#94a3b8",
};

export function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}

export function loadCanvasBg(): string {
  try {
    const raw = String(localStorage.getItem(CANVAS_BG_KEY) || "").trim().toLowerCase();
    if (raw === LEGACY_CANVAS_BG) {
      persistCanvasBg(DEFAULT_CANVAS_BG);
      return DEFAULT_CANVAS_BG;
    }
    if (isHexColor(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_CANVAS_BG;
}

export function persistCanvasBg(value: string) {
  try {
    localStorage.setItem(CANVAS_BG_KEY, value);
  } catch {
    /* ignore */
  }
}

export function loadLabelColors(): LabelColors {
  try {
    const raw = localStorage.getItem(LABEL_COLORS_KEY);
    if (!raw) return { ...DEFAULT_LABEL_COLORS };
    const parsed = JSON.parse(raw) as Partial<LabelColors>;
    return {
      name: isHexColor(String(parsed.name || "")) ? String(parsed.name).toLowerCase() : DEFAULT_LABEL_COLORS.name,
      edgeLabel: isHexColor(String(parsed.edgeLabel || ""))
        ? String(parsed.edgeLabel).toLowerCase()
        : DEFAULT_LABEL_COLORS.edgeLabel,
    };
  } catch {
    return { ...DEFAULT_LABEL_COLORS };
  }
}

export function persistLabelColors(value: LabelColors) {
  try {
    localStorage.setItem(LABEL_COLORS_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

export function loadVendorColors(): VendorColors {
  try {
    const raw = localStorage.getItem(VENDOR_COLORS_KEY);
    if (!raw) return { ...DEFAULT_VENDOR_COLORS };
    const parsed = JSON.parse(raw) as Partial<VendorColors>;
    const out = { ...DEFAULT_VENDOR_COLORS };
    for (const key of VENDOR_TONE_KEYS) {
      const c = String(parsed?.[key] || "").trim().toLowerCase();
      if (isHexColor(c)) out[key] = c;
    }
    return out;
  } catch {
    return { ...DEFAULT_VENDOR_COLORS };
  }
}

export function persistVendorColors(value: VendorColors) {
  try {
    localStorage.setItem(VENDOR_COLORS_KEY, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

export function loadBoolFlag(key: string, defaultValue: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return defaultValue;
    return raw === "1" || raw === "true";
  } catch {
    return defaultValue;
  }
}

export function persistBoolFlag(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export function loadAutoLayoutAfterDiscover(): boolean {
  return loadBoolFlag(AUTO_LAYOUT_DISCOVER_KEY, false);
}

export function persistAutoLayoutAfterDiscover(value: boolean) {
  persistBoolFlag(AUTO_LAYOUT_DISCOVER_KEY, value);
}

export type { CSSProperties };
