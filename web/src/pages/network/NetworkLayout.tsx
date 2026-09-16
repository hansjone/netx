import { Accordion } from "@heroui/react";
import type { Key } from "@react-types/shared";
import { useEffect, useMemo, useState } from "react";
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

function groupsForPath(pathname: string): Set<Key> {
  const keys = new Set<Key>();
  for (const g of NETWORK_NAV) {
    if (groupContainsPath(g.id, pathname)) keys.add(g.id);
  }
  return keys;
}

export function NetworkLayout() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [expandedKeys, setExpandedKeys] = useState<Set<Key>>(() => groupsForPath(pathname));

  useEffect(() => {
    setExpandedKeys((prev) => {
      const routeKeys = groupsForPath(pathname);
      if ([...routeKeys].every((k) => prev.has(k))) return prev;
      const next = new Set(prev);
      for (const k of routeKeys) next.add(k);
      return next;
    });
  }, [pathname]);

  const activeGroupIds = useMemo(() => {
    const ids = new Set<NetworkNavGroupId>();
    for (const g of NETWORK_NAV) {
      if (groupContainsPath(g.id, pathname)) ids.add(g.id);
    }
    return ids;
  }, [pathname]);

  return (
    <div className="network-shell">
      <aside className="network-nav" aria-label={t("network.title")}>
        <div className="network-nav__brand">{t("network.title")}</div>
        <nav className="network-nav__scroll">
          <Accordion
            className="network-nav__accordion"
            hideSeparator
            allowsMultipleExpanded
            expandedKeys={expandedKeys}
            onExpandedChange={setExpandedKeys}
          >
            {NETWORK_NAV.map((group) => {
              const activeGroup = activeGroupIds.has(group.id);
              return (
                <Accordion.Item
                  key={group.id}
                  id={group.id}
                  className={`network-nav__group${activeGroup ? " is-active-group" : ""}`}
                >
                  <Accordion.Heading>
                    <Accordion.Trigger className="network-nav__group-toggle">
                      <span className="network-nav__group-label">{t(group.labelKey)}</span>
                      <Accordion.Indicator className="network-nav__chevron" />
                    </Accordion.Trigger>
                  </Accordion.Heading>
                  <Accordion.Panel>
                    <Accordion.Body className="network-nav__panel-body">
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
                    </Accordion.Body>
                  </Accordion.Panel>
                </Accordion.Item>
              );
            })}
          </Accordion>
        </nav>
      </aside>
      <div className="network-main">
        <Outlet />
      </div>
    </div>
  );
}
