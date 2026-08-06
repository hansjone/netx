/** Clipboard helpers that work on http://LAN (non-secure) as well as HTTPS. */

export function isClipboardApiAvailable(): boolean {
  try {
    return Boolean(
      typeof window !== "undefined" &&
        window.isSecureContext &&
        navigator.clipboard &&
        typeof navigator.clipboard.writeText === "function",
    );
  } catch {
    return false;
  }
}

/** Write text; prefers Clipboard API, falls back to execCommand('copy'). */
export async function writeClipboardText(text: string): Promise<boolean> {
  const value = String(text || "");
  if (!value) return false;

  if (isClipboardApiAvailable() && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      /* fall through */
    }
  }

  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return Boolean(ok);
  } catch {
    return false;
  }
}

/**
 * Read clipboard via Clipboard API when available.
 * On insecure http://IP, returns null — caller must use a paste-event bridge.
 */
export async function readClipboardText(): Promise<string | null> {
  if (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    navigator.clipboard &&
    typeof navigator.clipboard.readText === "function"
  ) {
    try {
      return await navigator.clipboard.readText();
    } catch {
      return null;
    }
  }
  return null;
}
