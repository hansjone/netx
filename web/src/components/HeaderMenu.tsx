import { useI18n } from "../i18n";

/** Compact locale toggle: shows current language, click switches ZH ↔ EN. */
export function HeaderMenu() {
  const { t, locale, setLocale } = useI18n();
  const next = locale === "zh" ? "en" : "zh";
  const label = locale === "zh" ? "ZH" : "EN";

  return (
    <button
      type="button"
      className="header-menu__trigger header-menu__trigger--on-brand header-menu__trigger--locale"
      title={t("layout.switchLanguage")}
      aria-label={t("layout.switchLanguage")}
      onClick={() => setLocale(next)}
    >
      {label}
    </button>
  );
}
