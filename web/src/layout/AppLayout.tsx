import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Chip } from "@heroui/react";
import { AppsGridIcon } from "./AppsGridIcon";
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
            </>
          ) : null}
        </div>
        <div className="app-brand__actions">
          {user ? (
            <div className="app-brand__actions-group">
              <Button
                size="sm"
                variant="ghost"
                className="conn-pill conn-pill--on-brand conn-pill--tasks"
                aria-label={t("layout.activeTasks", { count: activeTaskCount })}
                onPress={() => openOrFocusModule({ moduleId: "audit", path: "/audit/tasks" })}
              >
                <svg viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 5h14M3 10h10M3 15h7" /></svg>
                <span className="app-brand__tasks-text">{t("layout.activeTasks", { count: activeTaskCount })}</span>
                <span className="app-brand__tasks-count" aria-hidden="true">{activeTaskCount}</span>
              </Button>
              <Chip size="sm" variant="soft" className="conn-pill conn-pill--on-brand conn-pill--user" title={user.role}>
                <Chip.Label>{user.username}</Chip.Label>
              </Chip>
              <Button
                size="sm"
                variant="ghost"
                className="header-menu__trigger header-menu__trigger--on-brand"
                onPress={() => void logout()}
              >
                {t("auth.logout")}
              </Button>
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
