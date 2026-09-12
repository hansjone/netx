import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

export type ToastState = { type: "ok" | "error"; text: string } | null;

type ToastApi = {
  toast: ToastState;
  showOk: (text: string) => void;
  showError: (text: string) => void;
  clear: () => void;
};

const ToastContext = createContext<ToastApi | null>(null);

function useToastState(autoHideMs: number): ToastApi {
  const [toast, setToast] = useState<ToastState>(null);

  const showOk = useCallback((text: string) => setToast({ type: "ok", text }), []);
  const showError = useCallback((text: string) => setToast({ type: "error", text }), []);
  const clear = useCallback(() => setToast(null), []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), autoHideMs);
    return () => window.clearTimeout(timer);
  }, [toast, autoHideMs]);

  return useMemo(() => ({ toast, showOk, showError, clear }), [toast, showOk, showError, clear]);
}

function ToastViewport({ toast }: { toast: NonNullable<ToastState> }) {
  if (typeof document === "undefined") return null;
  return createPortal(
    <div className={`app-toast app-toast--${toast.type}`} role="status" aria-live="polite">
      {toast.text}
    </div>,
    document.body,
  );
}

export function ToastProvider({ children, autoHideMs = 2600 }: { children: ReactNode; autoHideMs?: number }) {
  const api = useToastState(autoHideMs);
  return (
    <ToastContext.Provider value={api}>
      {children}
      {api.toast ? <ToastViewport toast={api.toast} /> : null}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
