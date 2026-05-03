import { useCallback, useEffect, useState } from "react";

export type ToastState = { type: "ok" | "error"; text: string } | null;

export function useToast(autoHideMs = 2600) {
  const [toast, setToast] = useState<ToastState>(null);

  const showOk = useCallback((text: string) => setToast({ type: "ok", text }), []);
  const showError = useCallback((text: string) => setToast({ type: "error", text }), []);
  const clear = useCallback(() => setToast(null), []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), autoHideMs);
    return () => clearTimeout(t);
  }, [toast, autoHideMs]);

  return { toast, showOk, showError, clear };
}

