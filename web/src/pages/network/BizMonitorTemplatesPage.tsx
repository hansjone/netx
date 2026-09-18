import { Button, Input, Modal } from "@heroui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  bizCompareListTemplates,
  bizMonitorCreateTemplate,
  bizMonitorDeleteTemplate,
  bizMonitorListTemplates,
  bizMonitorUpdateTemplate,
  formatErr,
} from "../../services/api";

type CompareTpl = { id: string; name: string };
type MonitorTpl = {
  id: string;
  name: string;
  compare_template_id: string;
  compare_template_name?: string;
  collect_metric_ids?: string[];
  defaults?: Record<string, unknown>;
  sheet_overrides?: unknown[];
  note?: string;
};

export function BizMonitorTemplatesPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();

  const [items, setItems] = useState<MonitorTpl[]>([]);
  const [compareTpls, setCompareTpls] = useState<CompareTpl[]>([]);
  const [busy, setBusy] = useState(false);
  const [listKw, setListKw] = useState("");
  const debouncedKw = useDebouncedValue(listKw, 250);

  const [editOpen, setEditOpen] = useState(false);
  const [editId, setEditId] = useState("");
  const [name, setName] = useState("");
  const [compareId, setCompareId] = useState("");
  const [note, setNote] = useState("");
  const [collectText, setCollectText] = useState("");
  const [defaultsText, setDefaultsText] = useState('{"dual_mode":"migrate_pair","out_of_expect":"strict"}');
  const [overridesText, setOverridesText] = useState("[]");

  const refresh = useCallback(async () => {
    const [mon, cmp] = await Promise.all([bizMonitorListTemplates(), bizCompareListTemplates()]);
    setItems((mon.items || []) as MonitorTpl[]);
    setCompareTpls(
      ((cmp.items || []) as Record<string, unknown>[]).map((x) => ({
        id: String(x.id || ""),
        name: String(x.name || x.id || ""),
      })),
    );
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await refresh();
      } catch (e) {
        showError(formatErr(e));
      }
    })();
  }, [refresh, showError]);

  const filtered = useMemo(() => {
    const kw = debouncedKw.trim().toLowerCase();
    if (!kw) return items;
    return items.filter((row) => {
      const blob = `${row.name} ${row.compare_template_name || ""} ${row.note || ""} ${row.compare_template_id}`.toLowerCase();
      return blob.includes(kw);
    });
  }, [items, debouncedKw]);

  const openCreate = () => {
    setEditId("");
    setName("");
    setCompareId(compareTpls[0]?.id || "");
    setNote("");
    setCollectText("");
    setDefaultsText('{"dual_mode":"migrate_pair","out_of_expect":"strict"}');
    setOverridesText("[]");
    setEditOpen(true);
  };

  const openEdit = (row: MonitorTpl) => {
    setEditId(row.id);
    setName(row.name || "");
    setCompareId(row.compare_template_id || "");
    setNote(row.note || "");
    setCollectText((row.collect_metric_ids || []).join(", "));
    setDefaultsText(JSON.stringify(row.defaults || {}, null, 2));
    setOverridesText(JSON.stringify(row.sheet_overrides || [], null, 2));
    setEditOpen(true);
  };

  const closeEdit = () => setEditOpen(false);

  const save = async () => {
    if (!name.trim()) {
      showError(t("bizMonitorTpl.needName"));
      return;
    }
    let defaults: Record<string, unknown> = {};
    let overrides: unknown[] = [];
    try {
      defaults = JSON.parse(defaultsText || "{}") as Record<string, unknown>;
      if (!defaults || typeof defaults !== "object" || Array.isArray(defaults)) {
        throw new Error("defaults");
      }
    } catch {
      showError(t("bizMonitorTpl.defaultsInvalid"));
      return;
    }
    try {
      overrides = JSON.parse(overridesText || "[]") as unknown[];
      if (!Array.isArray(overrides)) throw new Error("overrides");
    } catch {
      showError(t("bizMonitorTpl.overridesInvalid"));
      return;
    }
    const collect_metric_ids = collectText
      .split(/[,;\s]+/)
      .map((x) => x.trim())
      .filter(Boolean);
    const body = {
      name: name.trim(),
      compare_template_id: compareId,
      collect_metric_ids,
      defaults,
      sheet_overrides: overrides,
      note: note.trim(),
    };
    setBusy(true);
    try {
      if (editId) {
        await bizMonitorUpdateTemplate(editId, body);
        showOk(t("bizMonitorTpl.saved"));
      } else {
        await bizMonitorCreateTemplate(body);
        showOk(t("bizMonitorTpl.created"));
      }
      closeEdit();
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm(t("bizMonitorTpl.confirmDelete"))) return;
    setBusy(true);
    try {
      await bizMonitorDeleteTemplate(id);
      showOk(t("bizMonitorTpl.deleted"));
      await refresh();
    } catch (e) {
      showError(formatErr(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("bizMonitorTpl.title")}</h2>
        <div className="btn-row">
          <Button size="sm" variant="primary" onPress={openCreate}>
            {t("bizMonitorTpl.create")}
          </Button>
        </div>
      </div>
      <p className="panel__hint muted">{t("bizMonitorTpl.hint")}</p>

      <div className="pt-list">
        <div className="pt-list-kpis">
          <div className="pt-list-kpi">
            <div className="pt-list-kpi__label">{t("bizMonitorTpl.kpiTotal")}</div>
            <div className="pt-list-kpi__value">{items.length}</div>
          </div>
        </div>

        <div className="filter-inline">
          <Input
            value={listKw}
            placeholder={t("bizMonitorTpl.listFilterPh")}
            onChange={(e) => setListKw(e.target.value)}
          />
        </div>

        <div className="pt-list-table-wrap">
          <table className="data-table pt-list-table">
            <thead>
              <tr>
                <th>{t("bizMonitorTpl.colName")}</th>
                <th>{t("bizMonitorTpl.colCompare")}</th>
                <th>{t("bizMonitorTpl.colCollect")}</th>
                <th>{t("bizMonitorTpl.colNote")}</th>
                <th>{t("bizMonitorTpl.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div className="pt-list-task-name">{row.name}</div>
                  </td>
                  <td>
                    {row.compare_template_name || row.compare_template_id || "—"}
                    {row.compare_template_id ? (
                      <div className="muted" style={{ fontSize: 11 }}>
                        <Link to="/network/cutover/compare-templates">
                          {t("bizMonitorTpl.openCompare")}
                        </Link>
                      </div>
                    ) : null}
                  </td>
                  <td>
                    {(row.collect_metric_ids || []).length
                      ? (row.collect_metric_ids || []).join(", ")
                      : t("bizMonitorTpl.collectAll")}
                  </td>
                  <td className="muted">{row.note || "—"}</td>
                  <td>
                    <div className="pt-list-actions">
                      <Button size="sm" variant="primary" onPress={() => openEdit(row)}>
                        {t("bizMonitorTpl.edit")}
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        isDisabled={busy}
                        onPress={() => void remove(row.id)}
                      >
                        {t("bizMonitorTpl.delete")}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!filtered.length ? (
                <tr>
                  <td colSpan={5}>
                    <div className="pt-list-empty">{t("bizMonitorTpl.empty")}</div>
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      <AppModalShell open={editOpen} onClose={closeEdit} size="lg">
        <Modal.Header>
          <Modal.Heading>
            {editId ? t("bizMonitorTpl.edit") : t("bizMonitorTpl.create")}
          </Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            {t("bizMonitorTpl.formHint")}
          </p>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizMonitorTpl.colName")}</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <FieldSelect
            label={t("bizMonitorTpl.colCompare")}
            value={compareId}
            onChange={(e) => setCompareId(e.target.value)}
            fullWidth
            hint={t("bizMonitorTpl.compareHint")}
          >
            <option value="">{t("bizMonitorTpl.pickCompare")}</option>
            {compareTpls.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </FieldSelect>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizMonitorTpl.colCollect")}</span>
            <Input
              value={collectText}
              onChange={(e) => setCollectText(e.target.value)}
              placeholder={t("bizMonitorTpl.collectPh")}
            />
            <span className="ui-field__hint">{t("bizMonitorTpl.collectHint")}</span>
          </label>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizMonitorTpl.defaults")}</span>
            <textarea
              value={defaultsText}
              onChange={(e) => setDefaultsText(e.target.value)}
              rows={4}
              style={{ width: "100%", fontFamily: "ui-monospace, monospace", fontSize: 12 }}
            />
          </label>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizMonitorTpl.overrides")}</span>
            <textarea
              value={overridesText}
              onChange={(e) => setOverridesText(e.target.value)}
              rows={6}
              style={{ width: "100%", fontFamily: "ui-monospace, monospace", fontSize: 12 }}
            />
            <span className="ui-field__hint">{t("bizMonitorTpl.overridesHint")}</span>
          </label>
          <label className="ui-field ui-field--full">
            <span className="ui-field__label">{t("bizMonitorTpl.colNote")}</span>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
        </Modal.Body>
        <Modal.Footer>
          <Button size="sm" variant="secondary" onPress={closeEdit}>
            {t("bizMonitorTpl.cancel")}
          </Button>
          <Button size="sm" variant="primary" isDisabled={busy} onPress={() => void save()}>
            {t("bizMonitorTpl.save")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
