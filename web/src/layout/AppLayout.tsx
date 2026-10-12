import { Link, useLocation } from "react-router-dom";
import type { Key, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Dropdown } from "@heroui/react";
import { AppsGridIcon } from "./AppsGridIcon";
import { BrandMark } from "./BrandMark";
import { HeaderMenu } from "./HeaderMenu";
import { getPageTitleKey, isWorkbenchPath } from "../config/modules";
import { useAppWindowRegistration } from "../hooks/useAppWindowRegistration";
import { useI18n } from "../i18n";
import { fetchOpsTasks } from "../services/api";
import { openOrFocusModule } from "../utils/moduleWindows";
import { returnToWorkbench } from "../utils/workbench";
import { useAuth } from "../features/auth/AuthContext";

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
    // Idle installs: poll slowly; speed up only while tasks are active.
    refetchInterval: (q) => ((q.state.data?.active ?? 0) > 0 ? 4000 : 20_000),
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
  const activeTasks = opsTasksQuery.data?.active ?? 0;
  const activeTaskCount = opsTasksQuery.isLoading ? "…" : opsTasksQuery.isError ? "—" : activeTasks;
  const tasksBusy = !opsTasksQuery.isError && activeTasks > 0;
  const roleLabel = user?.role === "admin" ? t("auth.roleAdmin") : t("auth.roleUser");

  useAppWindowRegistration(pathname);

  const onUserAction = (key: Key) => {
    if (key === "logout") void logout();
  };

  const brand = (
    <>
      <BrandMark className="app-brand__mark" />
      <span className="app-brand__wordmark">NETX</span>
    </>
  );

  return (
    <div className="app app--shell">
      <header className="app-brand">
        <div className="app-brand__start">
          {onWorkbench ? (
            <Link to="/" className="app-brand__logo">{brand}</Link>
          ) : (
            <button type="button" className="app-brand__logo" onClick={returnToWorkbench} title={t("workbench.backToWorkbench")}>
              {brand}
            </button>
          )}
          {onWorkbench ? (
            <span className="app-brand__crumb">
              <span className="app-brand__sep" aria-hidden />
              <span className="app-brand__module-title">{t("workbench.consoleTitle")}</span>
            </span>
          ) : null}
          {!onWorkbench ? (
            <nav className="app-brand__crumb" aria-label={pageTitle}>
              <span className="app-brand__sep" aria-hidden />
              <Button
                isIconOnly
                size="sm"
                variant="ghost"
                className="app-brand__apps"
                onPress={returnToWorkbench}
                aria-label={t("workbench.backToWorkbench")}
              >
                <AppsGridIcon />
              </Button>
              <span className="app-brand__module-title">{pageTitle}</span>
            </nav>
          ) : null}
        </div>
        <div className="app-brand__actions">
          {user ? (
            <>
              <Button
                size="sm"
                variant="ghost"
                className={`app-brand__btn app-brand__tasks${tasksBusy ? " is-busy" : ""}`}
                aria-label={`${t("layout.activeTasks", { count: activeTaskCount })} · ${t("layout.activeTasksHint")}`}
                onPress={() => openOrFocusModule({ moduleId: "audit", path: "/audit/tasks" })}
              >
                <span className="app-brand__tasks-dot" aria-hidden="true" />
                <span className="app-brand__tasks-text">{t("layout.activeTasksLabel")}</span>
                <span className="app-brand__tasks-count">{activeTaskCount}</span>
              </Button>
              <HeaderMenu />
              <span className="app-brand__divider" aria-hidden="true" />
              <Dropdown>
                <Button size="sm" variant="ghost" className="app-brand__btn app-brand__user" aria-label={`${user.username} · ${roleLabel}`}>
                  <span className="app-brand__avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span>
                  <span className="app-brand__user-name">{user.username}</span>
                  <svg className="app-brand__chevron" viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
                </Button>
                <Dropdown.Popover className="app-brand__menu" placement="bottom end">
                  <div className="app-brand__menu-head">
                    <span className="app-brand__avatar app-brand__avatar--lg" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span>
                    <span className="app-brand__menu-id">
                      <strong>{user.username}</strong>
                      <span>{roleLabel}</span>
                    </span>
                  </div>
                  <Dropdown.Menu aria-label={user.username} onAction={onUserAction}>
                    <Dropdown.Item id="logout" textValue={t("auth.logout")} className="app-brand__menu-item">
                      <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 4h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-3M8 14l-4-4 4-4M4 10h9" /></svg>
                      {t("auth.logout")}
                    </Dropdown.Item>
                  </Dropdown.Menu>
                </Dropdown.Popover>
              </Dropdown>
            </>
          ) : (
            <HeaderMenu />
          )}
        </div>
      </header>

      <main className="app-main">{children}</main>
    </div>
  );
}
