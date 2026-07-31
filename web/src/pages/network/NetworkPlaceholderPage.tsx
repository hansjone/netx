import { useI18n } from "../../i18n";

type Props = {
  kind: "port-traffic";
};

export function NetworkPlaceholderPage({ kind }: Props) {
  const { t } = useI18n();
  const titleKey = kind === "port-traffic" ? "network.placeholder.portTrafficTitle" : "network.placeholder.title";
  const bodyKey = kind === "port-traffic" ? "network.placeholder.portTrafficBody" : "network.placeholder.body";

  return (
    <section className="panel network-placeholder">
      <h2>{t(titleKey)}</h2>
      <p className="muted">{t(bodyKey)}</p>
      {kind === "port-traffic" ? (
        <ul className="network-placeholder__list">
          <li>{t("network.placeholder.portTrafficStep1")}</li>
          <li>{t("network.placeholder.portTrafficStep2")}</li>
          <li>{t("network.placeholder.portTrafficStep3")}</li>
          <li>{t("network.placeholder.portTrafficStep4")}</li>
        </ul>
      ) : null}
    </section>
  );
}
