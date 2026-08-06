import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiGet, apiPost, clearAuthToken } from "../services/api";

export type AuthUser = {
  id: string;
  username: string;
  role: string;
  scopes?: string[];
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
  scopes: string[];
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  isAdmin: boolean;
  hasScope: (scope: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  // token is opaque for UI; cookie session means we only care about user presence.
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);

  const refreshMe = useCallback(async () => {
    clearAuthToken(); // drop any legacy localStorage tokens
    try {
      const data = await apiGet<{ user: AuthUser; scopes?: string[] }>("/v1/auth/me");
      setToken("cookie");
      setUser(data.user);
      setScopes(data.scopes || data.user.scopes || []);
    } catch {
      setToken(null);
      setUser(null);
      setScopes([]);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await refreshMe();
      setReady(true);
    })();
  }, [refreshMe]);

  const login = useCallback(async (username: string, password: string) => {
    const data = await apiPost<{ user: AuthUser }>("/v1/auth/login", {
      username,
      password,
    });
    clearAuthToken();
    setToken("cookie");
    setUser(data.user);
    setScopes(data.user.scopes || []);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiPost("/v1/auth/logout", {});
    } catch {
      // ignore
    }
    clearAuthToken();
    setToken(null);
    setUser(null);
    setScopes([]);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      token,
      user,
      scopes,
      login,
      logout,
      refreshMe,
      isAdmin: user?.role === "admin",
      hasScope: (scope: string) => scopes.includes(scope) || user?.role === "admin",
    }),
    [ready, token, user, scopes, login, logout, refreshMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
