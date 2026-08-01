/** SecureCRT-like keyword highlighting for WebCRT stdout (ANSI inject). */

export type KeywordRule = {
  id: string;
  pattern: string;
  regex: boolean;
};

export type KeywordHighlightConfig = {
  enabled: boolean;
  caseSensitive: boolean;
  /** Foreground (character) highlight color (hex). */
  color: string;
  keywords: KeywordRule[];
};

export const KEYWORD_HL_STORAGE_KEY = "netx.webcrt.keywordHighlight";

const DEFAULT_COLOR = "#ffff00";

export function defaultKeywordHighlightConfig(): KeywordHighlightConfig {
  return {
    enabled: false,
    caseSensitive: false,
    color: DEFAULT_COLOR,
    keywords: [],
  };
}

export function newKeywordId(): string {
  return `kw_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeHexColor(hex: string): string | null {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(String(hex || "").trim());
  return m ? `#${m[1].toLowerCase()}` : null;
}

export function loadKeywordHighlightConfig(): KeywordHighlightConfig {
  const base = defaultKeywordHighlightConfig();
  try {
    const raw = localStorage.getItem(KEYWORD_HL_STORAGE_KEY);
    if (!raw) return base;
    const j = JSON.parse(raw) as Partial<KeywordHighlightConfig>;
    const keywords = Array.isArray(j.keywords)
      ? j.keywords
          .map((k) => ({
            id: String((k as KeywordRule).id || newKeywordId()),
            pattern: String((k as KeywordRule).pattern || ""),
            regex: Boolean((k as KeywordRule).regex),
          }))
          .filter((k) => k.pattern.trim())
      : [];
    return {
      // Keywords imply active highlighting (avoids stale enabled:false in localStorage).
      enabled: keywords.length > 0,
      caseSensitive: Boolean(j.caseSensitive),
      color: normalizeHexColor(String(j.color || base.color)) || DEFAULT_COLOR,
      keywords,
    };
  } catch {
    return base;
  }
}

export function saveKeywordHighlightConfig(cfg: KeywordHighlightConfig): void {
  const keywords = (cfg.keywords || []).filter((k) => String(k.pattern || "").trim());
  localStorage.setItem(
    KEYWORD_HL_STORAGE_KEY,
    JSON.stringify({
      ...cfg,
      enabled: keywords.length > 0 && cfg.enabled !== false,
      keywords,
    }),
  );
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const n = normalizeHexColor(hex) || DEFAULT_COLOR;
  const v = parseInt(n.slice(1), 16);
  return { r: (v >> 16) & 255, g: (v >> 8) & 255, b: v & 255 };
}

/** Map highlight color to bold foreground (character color), not background. */
function sgrForColor(hex: string): { open: string; close: string } {
  const { r, g, b } = hexToRgb(hex);
  // 16-color bright fg fallback + truecolor fg for xterm.
  let brightFg = 93; // bright yellow
  if (g >= r && g >= b && g > 80) brightFg = 92; // bright green
  else if (r >= g && r >= b && r > 80 && g < 100) brightFg = 91; // bright red
  else if (b >= r && b >= g && b > 80) brightFg = 94; // bright blue
  else if (r > 180 && g > 180 && b < 120) brightFg = 93; // yellow
  const open = `\x1b[1;${brightFg}m\x1b[38;2;${r};${g};${b}m`;
  const close = "\x1b[22;39m";
  return { open, close };
}

type CompiledRule = {
  re: RegExp;
  open: string;
  close: string;
};

export function compileKeywordRules(config: KeywordHighlightConfig | null | undefined): CompiledRule[] {
  if (!config?.keywords?.length) return [];
  if (!config.enabled) return [];
  const { open, close } = sgrForColor(config.color || DEFAULT_COLOR);
  const flags = config.caseSensitive ? "g" : "gi";
  const out: CompiledRule[] = [];
  for (const kw of config.keywords) {
    const pattern = String(kw.pattern || "").trim();
    if (!pattern) continue;
    try {
      const source = kw.regex ? pattern : escapeRegExp(pattern);
      out.push({ re: new RegExp(source, flags), open, close });
    } catch {
      /* skip invalid regex */
    }
  }
  return out;
}

/** CSI / OSC / simple charset sequences — leave untouched. */
const ANSI_RE =
  /\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-2AB]|[>=])/g;

function highlightPlain(text: string, rules: CompiledRule[]): string {
  if (!text || !rules.length) return text;
  type Match = { start: number; end: number; open: string; close: string };
  const matches: Match[] = [];
  for (const rule of rules) {
    rule.re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = rule.re.exec(text)) !== null) {
      if (!m[0]) {
        rule.re.lastIndex += 1;
        continue;
      }
      matches.push({
        start: m.index,
        end: m.index + m[0].length,
        open: rule.open,
        close: rule.close,
      });
      if (!rule.re.global) break;
    }
  }
  if (!matches.length) return text;
  matches.sort((a, b) => a.start - b.start || b.end - a.end);
  const picked: Match[] = [];
  let cursor = 0;
  for (const m of matches) {
    if (m.start < cursor) continue;
    picked.push(m);
    cursor = m.end;
  }
  let out = "";
  let i = 0;
  for (const m of picked) {
    out += text.slice(i, m.start) + m.open + text.slice(m.start, m.end) + m.close;
    i = m.end;
  }
  out += text.slice(i);
  return out;
}

export function applyKeywordHighlight(
  text: string,
  config: KeywordHighlightConfig | null | undefined,
): string {
  if (!text) return text;
  const rules = compileKeywordRules(config);
  if (!rules.length) return text;
  let out = "";
  let last = 0;
  ANSI_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = ANSI_RE.exec(text)) !== null) {
    if (m.index > last) out += highlightPlain(text.slice(last, m.index), rules);
    out += m[0];
    last = m.index + m[0].length;
  }
  if (last < text.length) out += highlightPlain(text.slice(last), rules);
  return out;
}
