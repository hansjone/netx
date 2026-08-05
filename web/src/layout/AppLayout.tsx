import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppsGridIcon } from "../components/AppsGridIcon";
import { HeaderMenu } from "../components/HeaderMenu";
import { getPageTitleKey, isWorkbenchPath } from "../config/modules";
import { useAppWindowRegistration } from "../hooks/useAppWindowRegistration";
import { useI18n } from "../i18n";
import { fetchOpsTasks } from "../services/api";
import { openOrFocusModule } from "../utils/moduleWindows";
import { returnToWorkbench } from "../utils/workbench";
import { useAuth } from "../auth/AuthContext";

type Props = {
  children: ReactNode;
};

export function AppLayout({ children }: Props) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const { user, logout } = useAuth();
  const onWorkbench = isWorkbenchPath(pathname);
  const pageTitle = t(getPageTitleKey(pathname));
  const opsTasksQuery = useQuery({
    queryKey: ["opsTasks"],
    queryFn: fetchOpsTasks,
    enabled: Boolean(user),
    refetchInterval: 4000,
    staleTime: 1500,
  });
  const activeTasks = opsTasksQuery.data?.active ?? 0;

  useAppWindowRegistration(pathname);

  return (
    <div className="app app--shell">
      <header className="app-brand">
        <div className="app-brand__start">
          {onWorkbench ? (
            <Link to="/" className="app-brand__logo">
              NETX
            </Link>
          ) : (
            <span className="app-brand__logo app-brand__logo--static">NETX</span>
          )}
          {!onWorkbench ? (
            <>
              <span className="app-brand__sep" aria-hidden />
              <button
                type="button"
                className="app-brand__apps"
                onClick={returnToWorkbench}
                title={t("workbench.backToWorkbench")}
                aria-label={t("workbench.backToWorkbench")}
              >
                <AppsGridIcon />
              </button>
              <span className="app-brand__module-title">{pageTitle}</span>
            </>
          ) : null}
        </div>
        <div className="app-brand__actions">
          {user ? (
            <div className="app-brand__actions-group">
              <button
                type="button"
                className={`conn-pill conn-pill--on-brand conn-pill--tasks conn-pill--${activeTasks > 0 ? "up" : "unknown"}`}
                title={t("layout.activeTasksHint")}
                onClick={() => openOrFocusModule({ moduleId: "audit", path: "/audit/tasks" })}
              >
                {t("layout.activeTasks", { count: activeTasks })}
              </button>
              <span className="conn-pill conn-pill--on-brand conn-pill--user" title={user.role}>
                {user.username}
              </span>
              <button
                type="button"
                className="header-menu__trigger header-menu__trigger--on-brand"
                onClick={() => void logout()}
              >
                {t("auth.logout")}
              </button>
              <HeaderMenu />
            </div>
          ) : (
            <HeaderMenu />
          )}
        </div>
      </header>

      <main className="app-main">{children}</main>
    </div>
  );
}
