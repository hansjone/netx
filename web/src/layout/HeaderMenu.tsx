import { Button } from "@heroui/react";
import { useI18n } from "../i18n";

/** Compact locale toggle: shows current language, click switches ZH ↔ EN. */
export function HeaderMenu() {
  const { t, locale, setLocale } = useI18n();
  const next = locale === "zh" ? "en" : "zh";
  const label = locale === "zh" ? "ZH" : "EN";

  return (
    <Button
      size="sm"
      variant="ghost"
      className="app-brand__btn app-brand__locale"
      aria-label={t("layout.switchLanguage")}
      onPress={() => setLocale(next)}
    >
      <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7.25" /><path d="M2.75 10h14.5M10 2.75c2 2 3 4.4 3 7.25s-1 5.25-3 7.25c-2-2-3-4.4-3-7.25s1-5.25 3-7.25Z" /></svg>
      <span>{label}</span>
    </Button>
  );
}
