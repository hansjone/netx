export const AUTH_TOKEN_KEY = "netx_access_token";
export const AUTH_REFRESH_KEY = "netx_refresh_token";

/** Clear legacy localStorage tokens; browser auth now uses HttpOnly cookies. */
export const clearAuthToken = (): void => {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_REFRESH_KEY);
  } catch {
    // ignore
  }
};

/** @deprecated Cookie session — always null for browser UI. */
export const getAuthToken = (): string | null => null;

/** No-op kept for call-site compatibility during cookie migration. */
export const setAuthToken = (_token: string): void => {
  clearAuthToken();
};

export const setAuthTokens = (_access: string, _refresh?: string | null): void => {
  clearAuthToken();
};

export const fetchCreds: RequestCredentials = "include";

export const authHeaders = (extra?: Record<string, string>): Record<string, string> => {
  // Browser JWT rides HttpOnly cookies (credentials: include).
  // Authorization Bearer is only needed for non-browser API tokens if ever injected.
  return { accept: "application/json", ...(extra || {}) };
};

export const handleUnauthorized = (path: string): void => {
  if (path.startsWith("/v1/auth/login") || path.startsWith("/v1/auth/refresh")) return;
  clearAuthToken();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    const next = `${window.location.pathname}${window.location.search || ""}`;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
  }
};

let refreshInFlight: Promise<boolean> | null = null;

const tryRefreshAccessToken = async (): Promise<boolean> => {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        // Refresh token comes from HttpOnly cookie when body is empty.
        const res = await fetch("/v1/auth/refresh", {
          method: "POST",
          credentials: fetchCreds,
          headers: { accept: "application/json", "content-type": "application/json" },
          body: JSON.stringify({}),
        });
        if (!res.ok) {
          clearAuthToken();
          return false;
        }
        return true;
      } catch {
        clearAuthToken();
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
};

const shouldAttemptRefresh = (path: string): boolean =>
  !path.startsWith("/v1/auth/login") && !path.startsWith("/v1/auth/refresh");

export const parseApiResponse = async (res: Response): Promise<Record<string, unknown>> => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    const snippet = text.replace(/\s+/g, " ").trim().slice(0, 160);
    throw new Error(res.ok ? "invalid_json_response" : `${res.status} ${snippet || res.statusText}`);
  }
};

/** Preserve FastAPI structured ``detail`` objects (e.g. connect_failed + ne). */
export class ApiRequestError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(formatApiDetail(detail) || `${status}`);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
  }
}

/** User-facing message from API or thrown errors (prefers ApiRequestError.message). */
export function formatErr(err: unknown): string {
  if (err instanceof ApiRequestError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg?: unknown }).msg || "");
        }
        return "";
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.message === "string" && obj.message.trim()) return obj.message;
    if (typeof obj.error === "string" && obj.error.trim()) return obj.error;
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return detail == null ? "" : String(detail);
}

export const apiGet = async <T,>(path: string): Promise<T> => {
  const doFetch = () => fetch(path, { headers: authHeaders(), credentials: fetchCreds });
  let res = await doFetch();
  if (res.status === 401 && shouldAttemptRefresh(path) && (await tryRefreshAccessToken())) {
    res = await doFetch();
  }
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiPost = async <T,>(path: string, body: unknown): Promise<T> => {
  const doFetch = () =>
    fetch(path, {
      method: "POST",
      credentials: fetchCreds,
      headers: authHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    });
  let res = await doFetch();
  if (res.status === 401 && shouldAttemptRefresh(path) && (await tryRefreshAccessToken())) {
    res = await doFetch();
  }
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiPatch = async <T,>(path: string, body: unknown): Promise<T> => {
  const doFetch = () =>
    fetch(path, {
      method: "PATCH",
      credentials: fetchCreds,
      headers: authHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    });
  let res = await doFetch();
  if (res.status === 401 && shouldAttemptRefresh(path) && (await tryRefreshAccessToken())) {
    res = await doFetch();
  }
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiDelete = async <T,>(path: string): Promise<T> => {
  let res = await fetch(path, { method: "DELETE", headers: authHeaders(), credentials: fetchCreds });
  if (res.status === 401 && shouldAttemptRefresh(path) && (await tryRefreshAccessToken())) {
    res = await fetch(path, { method: "DELETE", headers: authHeaders(), credentials: fetchCreds });
  }
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};

export const apiPut = async <T,>(path: string, body: unknown): Promise<T> => {
  const doFetch = () =>
    fetch(path, {
      method: "PUT",
      credentials: fetchCreds,
      headers: authHeaders({ "content-type": "application/json" }),
      body: JSON.stringify(body),
    });
  let res = await doFetch();
  if (res.status === 401 && shouldAttemptRefresh(path) && (await tryRefreshAccessToken())) {
    res = await doFetch();
  }
  const data = await parseApiResponse(res);
  if (res.status === 401) {
    handleUnauthorized(path);
    throw new ApiRequestError(401, data.detail || "unauthorized");
  }
  if (!res.ok) throw new ApiRequestError(res.status, data.detail || `${res.status} ${path}`);
  return data as T;
};
