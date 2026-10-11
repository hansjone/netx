import type {
  ManagedNeItem,
} from "../../types";
import { apiDelete, apiPost, authHeaders, fetchCreds, handleUnauthorized } from "./http";

export type WebcrtSessionCreateResult = {
  session_id: string;
  ne_id: string;
  ne_name: string;
  ne_ip: string;
  protocol: string;
  cols: number;
  rows: number;
  ws_path: string;
  encoding?: string;
  state?: string;
  /** True when session used a vendor CLI hop (nested stelnet/telnet). */
  cli_hop?: boolean;
  /** True when SFTP channel opened on the same SSH transport. */
  sftp_ready?: boolean;
};

export const createWebcrtSession = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  cols?: number;
  rows?: number;
  encoding?: string;
  keepalive_sec?: number;
  post_login_commands?: string[];
  async_connect?: boolean;
  username?: string;
  password?: string;
}) => apiPost<WebcrtSessionCreateResult>("/v1/webcrt/sessions", body);

export type WebcrtQuickConnectResult = WebcrtSessionCreateResult & {
  ne: ManagedNeItem;
  ne_action: "created" | "updated" | "reused" | string;
  list_source: "webcrt" | "managed" | string;
};

export const quickConnectWebcrtSession = (body: {
  name?: string;
  ip_address: string;
  port?: number;
  protocol?: string;
  username?: string;
  password?: string;
  save_password?: boolean;
  /** Claim existing LLDP / incomplete ManagedNE → promote to webcrt. */
  ne_id?: string;
  cols?: number;
  rows?: number;
  encoding?: string;
  keepalive_sec?: number;
  async_connect?: boolean;
}) => apiPost<WebcrtQuickConnectResult>("/v1/webcrt/sessions/quick-connect", body);

export const closeWebcrtSession = (sessionId: string) =>
  apiDelete<{ ok: boolean; session_id: string; closed: boolean }>(
    `/v1/webcrt/sessions/${encodeURIComponent(sessionId)}`,
  );

/** Best-effort close during tab/window unload (fetch keepalive survives navigation). */
export function closeWebcrtSessionsKeepalive(sessionIds: string[]): void {
  const ids = [...new Set(sessionIds.map((id) => String(id || "").trim()).filter(Boolean))];
  if (!ids.length) return;
  const headers = authHeaders();
  for (const id of ids) {
    const path = `/v1/webcrt/sessions/${encodeURIComponent(id)}`;
    try {
      void fetch(path, { method: "DELETE", headers, credentials: fetchCreds, keepalive: true });
    } catch {
      /* ignore unload failures */
    }
  }
}

export type WebcrtSftpItem = {
  name: string;
  size: number;
  mtime: number;
  is_dir: boolean;
  mode?: string;
  owner?: string;
  group?: string;
  uid?: number;
  gid?: number;
};

export type WebcrtSftpListResult = {
  ne_id: string;
  ne_name: string;
  path: string;
  items: WebcrtSftpItem[];
  truncated?: boolean;
  max_entries?: number;
};

export const webcrtSftpList = (body: { ne_id?: string; ume_ne_id?: string; path?: string }) =>
  apiPost<WebcrtSftpListResult>("/v1/webcrt/sftp/list", body);

export const webcrtSftpMkdir = (body: { ne_id?: string; ume_ne_id?: string; path: string }) =>
  apiPost<{ ok: boolean; path: string; ne_id: string }>("/v1/webcrt/sftp/mkdir", body);

export const webcrtSftpRemove = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  path: string;
  recursive?: boolean;
}) => apiPost<{ ok: boolean; path: string; ne_id: string }>("/v1/webcrt/sftp/remove", body);

export const webcrtSftpRename = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  old_path: string;
  new_path: string;
}) => apiPost<{ ok: boolean; old_path: string; new_path: string; ne_id: string }>("/v1/webcrt/sftp/rename", body);

export const webcrtSftpChmod = (body: {
  ne_id?: string;
  ume_ne_id?: string;
  path: string;
  mode: string;
}) => apiPost<{ ok: boolean; path: string; mode: string; ne_id: string }>("/v1/webcrt/sftp/chmod", body);

export type WebcrtSftpTransferProgress = {
  loaded: number;
  total: number;
};

export type WebcrtSftpTransferOpts = {
  signal?: AbortSignal;
  onProgress?: (p: WebcrtSftpTransferProgress) => void;
  /** Extra attempts after the first failure. Default 2 (3 tries total). */
  retries?: number;
  onRetry?: (attempt: number, err: unknown) => void;
};

const isAbortError = (err: unknown): boolean => {
  const raw = String(err || "").toLowerCase();
  return raw.includes("aborted") || raw.includes("abort");
};

async function withSftpRetries<T>(
  run: () => Promise<T>,
  opts?: Pick<WebcrtSftpTransferOpts, "retries" | "signal" | "onRetry">,
): Promise<T> {
  const retries = Math.max(0, Number(opts?.retries ?? 2));
  let lastErr: unknown;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    if (opts?.signal?.aborted) throw new Error("aborted");
    try {
      return await run();
    } catch (err) {
      lastErr = err;
      if (isAbortError(err) || attempt >= retries) break;
      opts?.onRetry?.(attempt + 1, err);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr || "transfer_failed"));
}

async function webcrtSftpDownloadOnce(
  body: { ne_id?: string; ume_ne_id?: string; path: string },
  opts?: Pick<WebcrtSftpTransferOpts, "signal" | "onProgress">,
): Promise<Blob> {
  const path = "/v1/webcrt/sftp/download";
  const res = await fetch(path, {
    method: "POST",
    credentials: fetchCreds,
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal: opts?.signal,
  });
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new Error("401 unauthorized");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const total = Number(res.headers.get("content-length") || 0);
  if (!res.body) {
    const blob = await res.blob();
    opts?.onProgress?.({ loaded: blob.size, total: total || blob.size });
    return blob;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  try {
    for (;;) {
      if (opts?.signal?.aborted) {
        await reader.cancel().catch(() => undefined);
        throw new Error("aborted");
      }
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        loaded += value.byteLength;
        opts?.onProgress?.({ loaded, total });
      }
    }
  } catch (err) {
    if (opts?.signal?.aborted || isAbortError(err)) throw new Error("aborted");
    throw err;
  }
  return new Blob(chunks as BlobPart[], { type: "application/octet-stream" });
}

export async function webcrtSftpDownload(
  body: {
    ne_id?: string;
    ume_ne_id?: string;
    path: string;
  },
  optsOrProgress?: WebcrtSftpTransferOpts | ((p: WebcrtSftpTransferProgress) => void),
): Promise<Blob> {
  const opts: WebcrtSftpTransferOpts =
    typeof optsOrProgress === "function" ? { onProgress: optsOrProgress } : optsOrProgress || {};
  return withSftpRetries(
    () => webcrtSftpDownloadOnce(body, opts),
    opts,
  );
}

function webcrtSftpUploadOnce(
  body: {
    ne_id?: string;
    ume_ne_id?: string;
    remote_path: string;
    file: File;
  },
  opts?: Pick<WebcrtSftpTransferOpts, "signal" | "onProgress">,
): Promise<{ ok: boolean; path: string; size: number }> {
  const path = "/v1/webcrt/sftp/upload";
  const fd = new FormData();
  if (body.ne_id) fd.append("ne_id", body.ne_id);
  if (body.ume_ne_id) fd.append("ume_ne_id", body.ume_ne_id);
  fd.append("remote_path", body.remote_path);
  fd.append("file", body.file);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const onAbort = () => {
      try {
        xhr.abort();
      } catch {
        /* ignore */
      }
    };
    if (opts?.signal) {
      if (opts.signal.aborted) {
        reject(new Error("aborted"));
        return;
      }
      opts.signal.addEventListener("abort", onAbort, { once: true });
    }
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.responseType = "text";
    xhr.upload.onprogress = (ev) => {
      if (!opts?.onProgress) return;
      opts.onProgress({
        loaded: Number(ev.loaded || 0),
        total: ev.lengthComputable ? Number(ev.total || 0) : body.file.size || 0,
      });
    };
    xhr.onload = () => {
      opts?.signal?.removeEventListener("abort", onAbort);
      if (xhr.status === 401) {
        handleUnauthorized(path);
        reject(new Error("401 unauthorized"));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
        return;
      }
      try {
        resolve(JSON.parse(xhr.responseText || "{}") as { ok: boolean; path: string; size: number });
      } catch {
        reject(new Error("invalid_upload_response"));
      }
    };
    xhr.onerror = () => {
      opts?.signal?.removeEventListener("abort", onAbort);
      reject(new Error("network_error"));
    };
    xhr.onabort = () => {
      opts?.signal?.removeEventListener("abort", onAbort);
      reject(new Error("aborted"));
    };
    xhr.send(fd);
  });
}

export function webcrtSftpUpload(
  body: {
    ne_id?: string;
    ume_ne_id?: string;
    remote_path: string;
    file: File;
  },
  optsOrProgress?: WebcrtSftpTransferOpts | ((p: WebcrtSftpTransferProgress) => void),
): Promise<{ ok: boolean; path: string; size: number }> {
  const opts: WebcrtSftpTransferOpts =
    typeof optsOrProgress === "function" ? { onProgress: optsOrProgress } : optsOrProgress || {};
  return withSftpRetries(() => webcrtSftpUploadOnce(body, opts), opts);
}

export async function webcrtWsUrl(sessionId: string): Promise<string> {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const path = `/v1/webcrt/sessions/${encodeURIComponent(sessionId)}/ws`;
  let qs = "";
  try {
    const ticketResp = await apiPost<{ ticket: string; expires_in: number }>("/v1/webcrt/ws-ticket", {});
    if (ticketResp?.ticket) {
      qs = `?ws_ticket=${encodeURIComponent(ticketResp.ticket)}`;
    }
  } catch {
    qs = "";
  }
  // Optional override, e.g. ws://127.0.0.1:8890
  const override = String((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_NETX_WS_BASE || "").trim();
  if (override) {
    return `${override.replace(/\/$/, "")}${path}${qs}`;
  }
  // Vite/preview: HTTP is proxied, but WS proxy is often flaky — hit API directly.
  const port = window.location.port;
  if (port === "5173" || port === "4173") {
    const apiHost = window.location.hostname === "localhost" ? "127.0.0.1" : window.location.hostname;
    return `${proto}//${apiHost}:8890${path}${qs}`;
  }
  return `${proto}//${window.location.host}${path}${qs}`;
}
