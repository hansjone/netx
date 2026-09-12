import { Button, Input } from "@heroui/react";
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { FieldSelect } from "./ui/FieldSelect";

export type ListPagerProps = {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  pageSizeOptions?: number[];
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  disabled?: boolean;
  className?: string;
};

/** Shared denser pager: meta + prev/next + jump + optional page size. */
export function ListPager({
  page,
  pages,
  total,
  pageSize,
  pageSizeOptions = [10, 20, 50, 100, 200],
  onPageChange,
  onPageSizeChange,
  disabled = false,
  className = "",
}: ListPagerProps) {
  const { t } = useI18n();
  const safePages = Math.max(1, pages || 1);
  const [jumpDraft, setJumpDraft] = useState(String(page));

  useEffect(() => {
    setJumpDraft(String(page));
  }, [page]);

  const commitJump = () => {
    const n = Math.floor(Number(jumpDraft));
    if (!Number.isFinite(n)) {
      setJumpDraft(String(page));
      return;
    }
    const next = Math.min(safePages, Math.max(1, n));
    setJumpDraft(String(next));
    if (next !== page) onPageChange(next);
  };

  return (
    <div className={`pager pt-list-pager ${className}`.trim()}>
      <div className="pager__meta muted">
        {t("common.pagerMeta", {
          total: String(total),
          page: String(page),
          pages: String(safePages),
        })}
      </div>
      <div className="pager__controls btn-row">
        <Button
          size="sm"
          variant="secondary"
          className="pager__btn"
          isDisabled={disabled || page <= 1}
          onPress={() => onPageChange(Math.max(1, page - 1))}
        >
          {t("common.prevPage")}
        </Button>
        <label className="pager__jump">
          <span className="visually-hidden">{t("common.jumpPage")}</span>
          <Input
            className="pager__jump-input"
            type="number"
            min={1}
            max={safePages}
            value={jumpDraft}
            disabled={disabled}
            aria-label={t("common.jumpPage")}
            onChange={(e) => setJumpDraft(e.target.value)}
            onBlur={commitJump}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitJump();
              }
            }}
          />
          <span className="muted">/ {safePages}</span>
        </label>
        <Button
          size="sm"
          variant="secondary"
          className="pager__btn"
          isDisabled={disabled || page >= safePages}
          onPress={() => onPageChange(Math.min(safePages, page + 1))}
        >
          {t("common.nextPage")}
        </Button>
        {onPageSizeChange ? (
          <FieldSelect
            className="pager__size-field"
            aria-label={t("common.pageSize")}
            value={String(pageSize)}
            disabled={disabled}
            onChange={(e) => {
              const next = Number(e.target.value) || pageSizeOptions[0] || 50;
              onPageSizeChange(next);
            }}
          >
            {pageSizeOptions.map((n) => (
              <option key={n} value={String(n)}>
                {t("common.perPage", { n: String(n) })}
              </option>
            ))}
          </FieldSelect>
        ) : null}
      </div>
    </div>
  );
}
