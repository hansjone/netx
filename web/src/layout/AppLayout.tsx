import { Link, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { AppsGridIcon } from "../components/AppsGridIcon";
import { HeaderMenu } from "../components/HeaderMenu";
import { getPageTitleKey, isWorkbenchPath } from "../config/modules";
import { useAppWindowRegistration } from "../hooks/useAppWindowRegistration";
import { useI18n } from "../i18n";
import { returnToWorkbench } from "../utils/workbench";
import { useAuth } from "../auth/AuthContext";

type ConnLevel = "up" | "down" | "unknown";

type Props = {
  connections: {
    netxApi: ConnLevel;
    netxApiLatencyMs?: number;
    oclawBridge: ConnLevel;
    oclawBridgeLatencyMs?: number;
    oclawBridgeErrorKind?: string;
    oclawBridgeError?: string;
    oclawBridgeQueueSize?: number;
    oclawBridgePublishedOk?: number;
  };
  children: ReactNode;
};

export function AppLayout({ connections, children }: Props) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const { user, logout } = useAuth();
  const onWorkbench = isWorkbenchPath(pathname);
  const pageTitle = t(getPageTitleKey(pathname));
  const netxSuffix =
    typeof connections.netxApiLatencyMs === "number" ? ` (${connections.netxApiLatencyMs}ms)` : "";
  const oclawSuffix =
    connections.oclawBridge === "up"
      ? typeof connections.oclawBridgePublishedOk === "number"
        ? ` (pub ${connections.oclawBridgePublishedOk})`
        : ""
      : connections.oclawBridgeErrorKind
        ? ` (${connections.oclawBridgeErrorKind})`
        : typeof connections.oclawBridgeQueueSize === "number" && connections.oclawBridgeQueueSize > 0
          ? ` (q ${connections.oclawBridgeQueueSize})`
          : "";
  const oclawTitle =
    connections.oclawBridge === "down" && connections.oclawBridgeError
      ? connections.oclawBridgeError
      : undefined;

  useAppWindowRegistration(pathname);

  return (
    <div className="app app--shell">
      <header className="app-brand">
        <div className="app-brand__start">
          {onWorkbench ? (
            <Link to="/" className="app-brand__logo">
              NetX
            </Link>
          ) : (
            <span className="app-brand__logo app-brand__logo--static">NetX</span>
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
          <span className={`conn-pill conn-pill--on-brand conn-pill--${connections.netxApi}`}>
            {t("layout.netxApi")}: {connections.netxApi}
            {netxSuffix}
          </span>
          <span
            className={`conn-pill conn-pill--on-brand conn-pill--${connections.oclawBridge}`}
            title={oclawTitle}
          >
            {t("layout.oclawBridge")}: {connections.oclawBridge}
            {oclawSuffix}
          </span>
          {user ? (
            <span className="conn-pill conn-pill--on-brand conn-pill--up" title={user.role}>
              {user.username}
            </span>
          ) : null}
          {user ? (
            <button
              type="button"
              className="header-menu__trigger header-menu__trigger--on-brand"
              onClick={() => void logout()}
            >
              {t("auth.logout")}
            </button>
          ) : null}
          <HeaderMenu />
        </div>
      </header>

      <main className="app-main">{children}</main>
    </div>
  );
}
