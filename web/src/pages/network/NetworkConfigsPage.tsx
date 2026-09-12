import { Button, Input, Modal } from "@heroui/react";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ListPager } from "../../components/ListPager";
import { AppModalShell } from "../../components/ui/AppModalShell";
import { FieldSelect } from "../../components/ui/FieldSelect";
import { queryKeys } from "../../constants/queryKeys";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useToast } from "../../hooks/useToast";
import { useI18n } from "../../i18n";
import {
  downloadNeConfigSnapshot,
  fetchNeConfigSnapshotDetail,
  fetchNeConfigSnapshots,
  formatErr,
} from "../../services/api";
import type { NeConfigSnapshotMeta } from "../../types";
import { downloadCsv, fetchAllPages } from "../../utils/csvExport";
import { pageCount } from "../../utils/display";
import { openNewModuleWindow } from "../../utils/moduleWindows";
import { formatSystemTime } from "../../utils/time";

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function NetworkConfigsPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [keyword, setKeyword] = useState(() => String(searchParams.get("q") || ""));
  const [source, setSource] = useState("");
  const [vendor, setVendor] = useState("");
  const [selected, setSelected] = useState<{ source: string; id: string } | null>(null);
  const [tab, setTab] = useState<"primary" | "alt">("primary");
  const [exporting, setExporting] = useState("");
  const [exportingList, setExportingList] = useState(false);

  const debouncedKeyword = useDebouncedValue(keyword, 300);
  const debouncedVendor = useDebouncedValue(vendor, 300);

  useEffect(() => {
    const q = String(searchParams.get("q") || "").trim();
    if (!q) return;
    setKeyword(q);
    setPage(1);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (next.get("q") === q) next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams]);

  const listQuery = useQuery({
    queryKey: queryKeys.networkConfigs(page, debouncedKeyword, source, debouncedVendor, pageSize),
    queryFn: () =>
      fetchNeConfigSnapshots({
        page,
        pageSize,
        keyword: debouncedKeyword,
        source,
        vendor: debouncedVendor,
      }),
    staleTime: 5000,
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.networkConfigDetail(selected?.source || "", selected?.id || ""),
    queryFn: () => fetchNeConfigSnapshotDetail(selected!.source, selected!.id, "both"),
    enabled: Boolean(selected),
    staleTime: 10000,
  });

  const items = listQuery.data?.items ?? [];
  const total = Number(listQuery.data?.total || 0);
  const pages = pageCount(total, pageSize);
  const detail = detailQuery.data;
  const showAlt = Boolean(detail?.has_alt);
  const hasFilters = Boolean(keyword || source || vendor);

  const exportConfig = async (
    src: string,
    id: string,
    field: "primary" | "alt" | "both",
  ) => {
    const key = `${src}:${id}:${field}`;
    setExporting(key);
    try {
      await downloadNeConfigSnapshot(src, id, field);
      showOk(t("networkConfigs.exportOk"));
    } catch (err) {
      showError(formatErr(err));
    } finally {
      setExporting("");
    }
  };

  const exportListCsv = async () => {
    setExportingList(true);
    try {
      const rows = await fetchAllPages<NeConfigSnapshotMeta>({
        pageSize: 100,
        maxRows: 2000,
        fetchPage: (p, ps) =>
          fetchNeConfigSnapshots({
            page: p,
            pageSize: ps,
            keyword: debouncedKeyword,
            source,
            vendor: debouncedVendor,
          }),
      });
      downloadCsv(
        `${t("networkConfigs.exportListName")}-${new Date().toISOString().slice(0, 10)}.csv`,
        rows,
        [
          { key: "ne_name", header: t("networkConfigs.col.name"), value: (r) => r.ne_name || r.target_id },
          { key: "ne_ip", header: "IP" },
          { key: "vendor", header: t("networkConfigs.col.vendor") },
          { key: "source", header: t("networkConfigs.col.source") },
          { key: "plain_size", header: t("networkConfigs.col.size"), value: (r) => fmtBytes(r.plain_size) },
          {
            key: "collected_at",
            header: t("networkConfigs.col.collected"),
            value: (r) => (r.collected_at ? formatSystemTime(r.collected_at) : ""),
          },
          { key: "target_id", header: "ID" },
        ],
      );
      if (rows.length < total) {
        showOk(t("common.exportTruncated", { count: String(rows.length), total: String(total) }));
      } else {
        showOk(t("common.exportOk", { count: String(rows.length) }));
      }
    } catch (err) {
      showError(t("common.exportFailed") + ": " + formatErr(err));
    } finally {
      setExportingList(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel__toolbar">
        <h2>{t("networkConfigs.title")}</h2>
        <div className="btn-row">
          <Button
            size="sm"
            variant="secondary"
            isDisabled={exportingList || total === 0}
            onPress={() => void exportListCsv()}
          >
            {exportingList ? t("common.exporting") : t("common.exportCsv")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        <div className="filter-inline">
          <Input
            value={keyword}
            placeholder={t("networkConfigs.keywordPh")}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
          <FieldSelect
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setPage(1);
            }}
          >
            <option value="">{t("networkConfigs.allSource")}</option>
            <option value="managed">managed</option>
            <option value="ume">ume</option>
          </FieldSelect>
          <Input
            value={vendor}
            placeholder={t("networkConfigs.vendorPh")}
            onChange={(e) => {
              setVendor(e.target.value);
              setPage(1);
            }}
          />
          <Button
            size="sm"
            variant="tertiary"
            isDisabled={!hasFilters}
            onPress={() => {
              setKeyword("");
              setSource("");
              setVendor("");
              setPage(1);
            }}
          >
            {t("common.clearFilters")}
          </Button>
        </div>

        {listQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
        {listQuery.isError ? <p className="error-text">{t("common.opFailed")}</p> : null}

        {!items.length && !listQuery.isLoading ? (
          <div className="pt-list-empty">
            <p>{t("networkConfigs.empty")}</p>
          </div>
        ) : (
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("networkConfigs.col.name")}</th>
                  <th>IP</th>
                  <th>{t("networkConfigs.col.vendor")}</th>
                  <th>{t("networkConfigs.col.source")}</th>
                  <th>{t("networkConfigs.col.size")}</th>
                  <th>{t("networkConfigs.col.collected")}</th>
                  <th>{t("portTraffic.col.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const exportKey = `${row.source}:${row.target_id}:list`;
                  return (
                    <tr key={`${row.source}:${row.target_id}`}>
                      <td className="pt-list-task-name">{row.ne_name || row.target_id}</td>
                      <td className="pt-list-num">{row.ne_ip}</td>
                      <td>{row.vendor || "—"}</td>
                      <td>{row.source}</td>
                      <td className="pt-list-num">{fmtBytes(row.plain_size)}</td>
                      <td className="pt-list-time">
                        {row.collected_at ? formatSystemTime(row.collected_at) : "—"}
                      </td>
                      <td>
                        <div className="btn-row pt-list-actions table-actions">
                          <Button
                            size="sm"
                            variant="secondary"
                            onPress={() => {
                              setSelected({ source: row.source, id: row.target_id });
                              setTab("primary");
                            }}
                          >
                            {t("networkConfigs.view")}
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            isDisabled={
                              exporting === exportKey ||
                              exporting.startsWith(`${row.source}:${row.target_id}:`)
                            }
                            onPress={() =>
                              void exportConfig(
                                row.source,
                                row.target_id,
                                row.has_alt ? "both" : "primary",
                              )
                            }
                          >
                            {t("networkConfigs.export")}
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            onPress={() => {
                              const path =
                                row.source === "ume"
                                  ? `/webcrt?ne_id=${encodeURIComponent(row.target_id)}&source=ume`
                                  : `/webcrt?ne_id=${encodeURIComponent(row.target_id)}`;
                              openNewModuleWindow({ moduleId: "webcrt", path });
                            }}
                          >
                            WebCRT
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <ListPager
          page={page}
          pages={pages}
          total={total}
          pageSize={pageSize}
          pageSizeOptions={[10, 20, 50, 100]}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          disabled={listQuery.isLoading}
        />
      </div>

      <AppModalShell
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        size="lg"
        className="nm-config-modal"
      >
        <Modal.Header>
          <Modal.Heading>{detail?.ne_name || selected?.id || t("networkConfigs.view")}</Modal.Heading>
          <Modal.CloseTrigger />
        </Modal.Header>
        <Modal.Body className="flex flex-col gap-3">
          {selected ? (
            <p className="muted">
              {selected.source} · {detail?.ne_ip || "—"} · {detail?.vendor || "—"}
            </p>
          ) : null}
          {detailQuery.isLoading ? <p className="muted">{t("common.refreshing")}</p> : null}
          {showAlt ? (
            <div className="btn-row nm-config-modal__tabs">
              <Button
                size="sm"
                variant={tab === "primary" ? "primary" : "secondary"}
                onPress={() => setTab("primary")}
              >
                {t("networkConfigs.tabSet")}
              </Button>
              <Button
                size="sm"
                variant={tab === "alt" ? "primary" : "secondary"}
                onPress={() => setTab("alt")}
              >
                {t("networkConfigs.tabHier")}
              </Button>
            </div>
          ) : null}
          <textarea
            className="nm-config-modal__body"
            readOnly
            spellCheck={false}
            value={tab === "alt" ? detail?.config_alt_text || "" : detail?.config_text || ""}
          />
          <p className="muted nm-config-modal__meta">
            SHA-256: {tab === "alt" ? detail?.config_alt_sha256 : detail?.config_sha256} ·{" "}
            {fmtBytes(tab === "alt" ? detail?.plain_alt_size || 0 : detail?.plain_size || 0)}
          </p>
        </Modal.Body>
        <Modal.Footer>
          {selected ? (
            <>
              <Button
                variant="secondary"
                isDisabled={Boolean(exporting)}
                onPress={() =>
                  void exportConfig(
                    selected.source,
                    selected.id,
                    showAlt ? (tab === "alt" ? "alt" : "primary") : "primary",
                  )
                }
              >
                {t("networkConfigs.export")}
              </Button>
              {showAlt ? (
                <Button
                  variant="secondary"
                  isDisabled={Boolean(exporting)}
                  onPress={() => void exportConfig(selected.source, selected.id, "both")}
                >
                  {t("networkConfigs.exportBoth")}
                </Button>
              ) : null}
            </>
          ) : null}
          <Button variant="tertiary" onPress={() => setSelected(null)}>
            {t("networkConfigs.close")}
          </Button>
        </Modal.Footer>
      </AppModalShell>
    </section>
  );
}
