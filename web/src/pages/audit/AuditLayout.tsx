import { NavLink, Outlet } from "react-router-dom";
import { AUDIT_NAV } from "../../config/auditNav";
import { useI18n } from "../../i18n";

export function AuditLayout() {
  const { t } = useI18n();

  return (
    <div className="network-shell audit-shell">
      <aside className="network-nav" aria-label={t("audit.title")}>
        <div className="network-nav__brand">{t("audit.title")}</div>
        <nav className="network-nav__scroll">
          <ul className="network-nav__list network-nav__list--flat">
            {AUDIT_NAV.map((item) => (
              <li key={item.id}>
                <NavLink
                  to={item.path}
                  className={({ isActive }) => `network-nav__link${isActive ? " is-active" : ""}`}
                >
                  {t(item.labelKey)}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="network-main">
        <Outlet />
      </div>
    </div>
  );
}
