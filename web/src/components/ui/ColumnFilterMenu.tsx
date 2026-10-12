import { Button, Input } from "@heroui/react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { useI18n } from "../../i18n";
import type { ColumnValueFilter } from "../../types/tableFilters";

const EMPTY_FILTER_OPTIONS: string[] = [];
const identity = (value: string) => value;

export function ColumnFilterMenu({ label, value, queryKey, loadOptions, onApply, formatValue = identity }: {
  label: string;
  value: ColumnValueFilter;
  queryKey: readonly unknown[];
  loadOptions: () => Promise<string[]>;
  onApply: (value: ColumnValueFilter) => void;
  formatValue?: (value: string) => string;
}) {
  const { t } = useI18n();
  const placeholder = t("networkDevices.columnFilterPh");
  const selectAllLabel = t("networkDevices.selectAllColumnValues");
  const applyLabel = t("networkDevices.applyColumnFilter");
  const clearLabel = t("networkDevices.clearColumnFilter");
  const blankLabel = t("networkDevices.blankFilterValue");
  const loadingLabel = t("networkDevices.loadingFilterOptions");
  const noOptionsLabel = t("networkDevices.noFilterOptions");
  const cancelLabel = t("networkDevices.cancelColumnFilter");
  const errorLabel = t("networkDevices.filterOptionsError");
  const retryLabel = t("networkDevices.retryFilterOptions");
  const moreLabel = t("networkDevices.moreFilterOptions");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ColumnValueFilter>(value);
  const [optionSearch, setOptionSearch] = useState("");
  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [renderLimit, setRenderLimit] = useState(100);
  const [portalContainer, setPortalContainer] = useState<Element | null>(null);
  const optionsQuery = useQuery({
    queryKey,
    queryFn: loadOptions,
    enabled: open,
    staleTime: 30000,
  });
  const options = optionsQuery.data ?? EMPTY_FILTER_OPTIONS;
  const visibleOptions = useMemo(() => {
    const search = optionSearch.trim().toLocaleLowerCase();
    return options.filter((option) => formatValue(option).toLocaleLowerCase().includes(search));
  }, [options, optionSearch, formatValue]);
  const draftValues = new Set(draft?.values);
  const selectedVisibleCount = visibleOptions.filter((option) =>
    draft === null
      ? true
      : draft.mode === "include"
        ? draftValues.has(option)
        : !draftValues.has(option),
  ).length;
  const allVisibleSelected = visibleOptions.length > 0 && selectedVisibleCount === visibleOptions.length;
  const someVisibleSelected = selectedVisibleCount > 0 && !allVisibleSelected;

  useLayoutEffect(() => {
    if (!open) return;
    const positionMenu = () => {
      const trigger = triggerRef.current;
      const menu = menuRef.current;
      if (!trigger || !menu) return;
      const rect = trigger.getBoundingClientRect();
      const width = menu.offsetWidth;
      const height = menu.offsetHeight;
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
      const below = rect.bottom + 6;
      menu.style.top = `${Math.max(8, below + height <= window.innerHeight - 8 ? below : rect.top - height - 6)}px`;
    };
    positionMenu();
    const observer = new ResizeObserver(positionMenu);
    if (menuRef.current) observer.observe(menuRef.current);
    window.addEventListener("resize", positionMenu);
    document.addEventListener("scroll", positionMenu, true);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", positionMenu);
      document.removeEventListener("scroll", positionMenu, true);
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node) && !menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const apply = (next: ColumnValueFilter = draft) => {
    onApply(next);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const toggleOption = (option: string) => {
    if (draft === null) {
      setDraft({ mode: "exclude", values: [option] });
      return;
    }
    if (draft.mode === "exclude") {
      const values = draft.values.includes(option)
        ? draft.values.filter((item) => item !== option)
        : [...draft.values, option];
      setDraft(values.length ? { mode: "exclude", values } : null);
      return;
    }
    const values = draft.values.includes(option)
      ? draft.values.filter((item) => item !== option)
      : [...draft.values, option];
    setDraft({ mode: "include", values });
  };

  const isChecked = (option: string) => {
    if (draft === null) return true;
    return draft.mode === "include"
      ? draftValues.has(option)
      : !draftValues.has(option);
  };

  const setVisibleSelection = (selected: boolean) => {
    if (!optionSearch.trim()) {
      setDraft(selected ? null : { mode: "include", values: [] });
      return;
    }
    const visible = new Set(visibleOptions);
    if (draft === null) {
      if (!selected) setDraft({ mode: "exclude", values: visibleOptions });
      return;
    }
    if (draft.mode === "include") {
      const hiddenSelections = draft.values.filter((option) => !visible.has(option));
      const values = selected
        ? [...new Set([...hiddenSelections, ...visibleOptions])]
        : hiddenSelections;
      setDraft({ mode: "include", values });
      return;
    }
    const excluded = new Set(draft.values);
    for (const option of visibleOptions) {
      if (selected) excluded.delete(option);
      else excluded.add(option);
    }
    setDraft(excluded.size ? { mode: "exclude", values: [...excluded] } : null);
  };

  return (
    <span className="ne-column-filter" ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className={`ne-column-filter__trigger${value ? " is-active" : ""}`}
        aria-label={`${label}: ${placeholder}`}
        title={`${label}: ${placeholder}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          if (!open) {
            setPortalContainer(rootRef.current?.closest(".app-heroui-modal__dialog, .network-main") ?? document.body);
            setDraft(value);
            setOptionSearch("");
            setRenderLimit(100);
          }
          setOpen((current) => !current);
        }}
      >
        ▾
      </button>
      {open && portalContainer ? createPortal(
        <div className="ne-column-filter__menu" ref={menuRef} role="dialog" aria-label={label}
          onKeyDownCapture={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
              triggerRef.current?.focus();
            }
          }}>
          <Input
            autoFocus
            aria-label={placeholder}
            placeholder={placeholder}
            value={optionSearch}
            onChange={(event) => {
              setOptionSearch(event.target.value);
              setRenderLimit(100);
            }}
          />
          <label
            className="ne-column-filter__select-all"
          >
            <input
              ref={(input) => { if (input) input.indeterminate = someVisibleSelected; }}
              type="checkbox"
              checked={allVisibleSelected}
              disabled={!visibleOptions.length || optionsQuery.isFetching || optionsQuery.isError}
              onChange={(event) => setVisibleSelection(event.target.checked)}
            />
            <span>{selectAllLabel} ({selectedVisibleCount}/{visibleOptions.length})</span>
          </label>
          <div className="ne-column-filter__options">
            {optionsQuery.isLoading ? <span className="muted">{loadingLabel}</span> : null}
            {!optionsQuery.isLoading && optionsQuery.isError ? (
              <div className="error-text">
                {errorLabel}
                <Button size="sm" variant="tertiary" onPress={() => { void optionsQuery.refetch(); }}>{retryLabel}</Button>
              </div>
            ) : null}
            {!optionsQuery.isLoading && !optionsQuery.isError && !visibleOptions.length ? (
              <span className="muted">{noOptionsLabel}</span>
            ) : null}
            {visibleOptions.slice(0, renderLimit).map((option) => (
              <label className="ne-column-filter__option" key={option} title={option ? formatValue(option) : blankLabel}>
                <input
                  type="checkbox"
                  checked={isChecked(option)}
                  disabled={optionsQuery.isFetching || optionsQuery.isError}
                  onChange={() => toggleOption(option)}
                />
                <span>{option ? formatValue(option) : blankLabel}</span>
              </label>
            ))}
            {visibleOptions.length > renderLimit ? (
              <Button size="sm" variant="tertiary" onPress={() => setRenderLimit((limit) => limit + 100)}>{moreLabel}</Button>
            ) : null}
          </div>
          <div className="ne-column-filter__actions">
            <Button size="sm" variant="tertiary" onPress={() => apply(null)}>{clearLabel}</Button>
            <Button size="sm" variant="tertiary" onPress={() => { setOpen(false); triggerRef.current?.focus(); }}>{cancelLabel}</Button>
            <Button size="sm" variant="secondary" isDisabled={optionsQuery.isFetching || optionsQuery.isError} onPress={() => apply()}>{applyLabel}</Button>
          </div>
        </div>,
        portalContainer,
      ) : null}
    </span>
  );
}
