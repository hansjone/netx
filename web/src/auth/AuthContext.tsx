import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiGet, apiPost, clearAuthToken, getAuthToken, setAuthToken } from "../services/api";

export type AuthUser = {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
  must_change_password?: boolean;
  created_by?: string;
  created_at?: string | null;
  updated_at?: string | null;
};

type AuthState = {
  ready: boolean;
  token: string | null;
  user: AuthUser | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  isAdmin: boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(() => getAuthToken());
  const [user, setUser] = useState<AuthUser | null>(null);

  const refreshMe = useCallback(async () => {
    const tok = getAuthToken();
    if (!tok) {
      setToken(null);
      setUser(null);
      return;
    }
    try {
      const data = await apiGet<{ user: AuthUser }>("/v1/auth/me");
      setToken(tok);
      setUser(data.user);
    } catch {
      clearAuthToken();
      setToken(null);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await refreshMe();
      setReady(true);
    })();
  }, [refreshMe]);

  const login = useCallback(async (username: string, password: string) => {
    const data = await apiPost<{ access_token: string; user: AuthUser }>("/v1/auth/login", {
      username,
      password,
    });
    setAuthToken(data.access_token);
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      if (getAuthToken()) {
        await apiPost("/v1/auth/logout", {});
      }
    } catch {
      // ignore
    }
    clearAuthToken();
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      token,
      user,
      login,
      logout,
      refreshMe,
      isAdmin: user?.role === "admin",
    }),
    [ready, token, user, login, logout, refreshMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
