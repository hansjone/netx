import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { NETWORK_NAV, type NetworkNavGroupId } from "../../config/networkNav";
import { useI18n } from "../../i18n";

function groupContainsPath(groupId: NetworkNavGroupId, pathname: string): boolean {
  const group = NETWORK_NAV.find((g) => g.id === groupId);
  if (!group) return false;
  return group.items.some(
    (item) => pathname === item.path || pathname.startsWith(`${item.path}/`),
  );
}

export function NetworkLayout() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [openGroups, setOpenGroups] = useState<Record<NetworkNavGroupId, boolean>>(() => {
    const init = {} as Record<NetworkNavGroupId, boolean>;
    for (const g of NETWORK_NAV) {
      // Default collapsed; only the group owning the current route starts open.
      init[g.id] = groupContainsPath(g.id, pathname);
    }
    return init;
  });

  useEffect(() => {
    for (const g of NETWORK_NAV) {
      if (groupContainsPath(g.id, pathname)) {
        setOpenGroups((prev) => (prev[g.id] ? prev : { ...prev, [g.id]: true }));
      }
    }
  }, [pathname]);

  const toggleGroup = (id: NetworkNavGroupId) => {
    setOpenGroups((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="network-shell">
      <aside className="network-nav" aria-label={t("network.title")}>
        <nav className="network-nav__scroll">
          {NETWORK_NAV.map((group) => {
            const open = Boolean(openGroups[group.id]);
            const activeGroup = groupContainsPath(group.id, pathname);
            return (
              <div
                key={group.id}
                className={`network-nav__group${open ? " is-open" : ""}${activeGroup ? " is-active-group" : ""}`}
              >
                <button
                  type="button"
                  className="network-nav__group-toggle"
                  aria-expanded={open}
                  onClick={() => toggleGroup(group.id)}
                >
                  <span className="network-nav__chevron" aria-hidden>
                    {open ? "▾" : "▸"}
                  </span>
                  <span className="network-nav__group-label">{t(group.labelKey)}</span>
                </button>
                {open ? (
                  <ul className="network-nav__list">
                    {group.items.map((item) => (
                      <li key={item.id}>
                        <NavLink
                          to={item.path}
                          className={({ isActive }) =>
                            `network-nav__link${isActive ? " is-active" : ""}`
                          }
                          end={
                            item.path === "/network/devices" ||
                            item.path === "/network/tasks/port-traffic"
                          }
                        >
                          {t(item.labelKey)}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </nav>
      </aside>
      <div className="network-main">
        <Outlet />
      </div>
    </div>
  );
}
