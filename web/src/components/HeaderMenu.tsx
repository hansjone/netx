import { useEffect, useRef, useState } from "react";
import { useI18n, type Locale } from "../i18n";

export function HeaderMenu() {
  const { t, locale, setLocale } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (ev: MouseEvent) => {
      if (!rootRef.current?.contains(ev.target as Node)) setOpen(false);
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (next: Locale) => {
    setLocale(next);
    setOpen(false);
  };

  return (
    <div className="header-menu header-menu--on-brand" ref={rootRef}>
      <button
        type="button"
        className="header-menu__trigger header-menu__trigger--on-brand"
        aria-label={t("layout.moreMenu")}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ⋯
      </button>
      {open ? (
        <div className="header-menu__panel header-menu__panel--light" role="menu">
          <div className="header-menu__label header-menu__label--light">{t("layout.language")}</div>
          <button
            type="button"
            role="menuitemradio"
            className={`header-menu__item header-menu__item--light${locale === "zh" ? " header-menu__item--active-light" : ""}`}
            aria-checked={locale === "zh"}
            onClick={() => pick("zh")}
          >
            {t("layout.langZh")}
          </button>
          <button
            type="button"
            role="menuitemradio"
            className={`header-menu__item header-menu__item--light${locale === "en" ? " header-menu__item--active-light" : ""}`}
            aria-checked={locale === "en"}
            onClick={() => pick("en")}
          >
            {t("layout.langEn")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
