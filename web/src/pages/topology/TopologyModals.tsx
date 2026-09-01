import type { DragEvent } from "react";
import type { TopologyOutsidePeer } from "../../types";
import type { PaletteItem, PaletteSource } from "./pageTypes";
import { AddNePaletteDialog } from "./modals/AddNePaletteDialog";
import { CreateNeModeDialog } from "./modals/CreateNeModeDialog";
import { NewRootDialog } from "./modals/NewRootDialog";
import {
  PlaceholderCreateDialog,
  type PlaceholderCreateDialogState,
} from "./modals/PlaceholderCreateDialog";
import { OutsidePeersDialog } from "./modals/OutsidePeersDialog";
import { ManagedNeFormDialog } from "../managedNe/ManagedNeFormDialog";
import { ManagedNeConnectDetailDialog } from "../managedNe/ManagedNeConnectDetailDialog";
import type { ManagedNeItem } from "../../types";
import type { ManagedNeFormState } from "../managedNe/formState";

export type { PlaceholderCreateDialogState as CreateNeDialogState };

export type TopologyModalsProps = {
  canvasMode: boolean;
  newRootDialog: { name: string } | null;
  onNewRootNameChange: (name: string) => void;
  onCloseNewRoot: () => void;
  onSubmitNewRoot: () => void;
  createRegionPending: boolean;

  createNeModeOpen: boolean;
  onCloseCreateNeMode: () => void;
  onPickCreateManaged: () => void;
  onPickCreatePlaceholder: () => void;

  placeholderDialog: PlaceholderCreateDialogState | null;
  placeholderBusy: boolean;
  onPlaceholderChange: (patch: Partial<Pick<PlaceholderCreateDialogState, "name" | "ip_address">>) => void;
  onClosePlaceholder: () => void;
  onSubmitPlaceholder: () => void;

  managedFormOpen: boolean;
  managedFormInitial?: Partial<ManagedNeFormState>;
  onCloseManagedForm: () => void;
  onManagedFormSaved: (item: ManagedNeItem) => void;

  connectDetailRow: ManagedNeItem | null;
  onCloseConnectDetail: () => void;
  onConnectRetestSubmitted: (rowId: string) => void;

  outsidePeersOpen: boolean;
  outsidePeers: TopologyOutsidePeer[];
  outsidePeersVisible: TopologyOutsidePeer[];
  outsidePeerQuery: string;
  onOutsidePeerQueryChange: (q: string) => void;
  outsidePeerSelectedIds: string[];
  onOutsidePeerSelectedIdsChange: (ids: string[] | ((prev: string[]) => string[])) => void;
  outsidePeerNameById: Map<string, string>;
  outsidePeersAdding: boolean;
  onCloseOutsidePeers: () => void;
  onAddOutsidePeers: (ids: string[]) => void;

  addNeOpen: boolean;
  paletteSource: PaletteSource;
  onPaletteSourceChange: (source: PaletteSource) => void;
  keyword: string;
  onKeywordChange: (keyword: string) => void;
  paletteVisible: PaletteItem[];
  paletteSelectedKeys: string[];
  onPaletteSelectedKeysChange: (keys: string[] | ((prev: string[]) => string[])) => void;
  paletteLoading: boolean;
  paletteAdding: boolean;
  onCloseAddNe: () => void;
  onPaletteDragStart: (e: DragEvent, item: PaletteItem) => void;
  onAddSelectedPalette: () => void;
};

/** Thin composer — keep individual dialogs in `modals/` / `managedNe/`. */
export function TopologyModals({
  canvasMode,
  newRootDialog,
  onNewRootNameChange,
  onCloseNewRoot,
  onSubmitNewRoot,
  createRegionPending,
  createNeModeOpen,
  onCloseCreateNeMode,
  onPickCreateManaged,
  onPickCreatePlaceholder,
  placeholderDialog,
  placeholderBusy,
  onPlaceholderChange,
  onClosePlaceholder,
  onSubmitPlaceholder,
  managedFormOpen,
  managedFormInitial,
  onCloseManagedForm,
  onManagedFormSaved,
  connectDetailRow,
  onCloseConnectDetail,
  onConnectRetestSubmitted,
  outsidePeersOpen,
  outsidePeers,
  outsidePeersVisible,
  outsidePeerQuery,
  onOutsidePeerQueryChange,
  outsidePeerSelectedIds,
  onOutsidePeerSelectedIdsChange,
  outsidePeerNameById,
  outsidePeersAdding,
  onCloseOutsidePeers,
  onAddOutsidePeers,
  addNeOpen,
  paletteSource,
  onPaletteSourceChange,
  keyword,
  onKeywordChange,
  paletteVisible,
  paletteSelectedKeys,
  onPaletteSelectedKeysChange,
  paletteLoading,
  paletteAdding,
  onCloseAddNe,
  onPaletteDragStart,
  onAddSelectedPalette,
}: TopologyModalsProps) {
  return (
    <>
      <NewRootDialog
        dialog={newRootDialog}
        pending={createRegionPending}
        onNameChange={onNewRootNameChange}
        onClose={onCloseNewRoot}
        onSubmit={onSubmitNewRoot}
      />

      {canvasMode ? (
        <>
          <CreateNeModeDialog
            open={createNeModeOpen}
            onClose={onCloseCreateNeMode}
            onPickManaged={onPickCreateManaged}
            onPickPlaceholder={onPickCreatePlaceholder}
          />
          <PlaceholderCreateDialog
            dialog={placeholderDialog}
            busy={placeholderBusy}
            onChange={onPlaceholderChange}
            onClose={onClosePlaceholder}
            onSubmit={onSubmitPlaceholder}
          />
          <ManagedNeFormDialog
            open={managedFormOpen}
            editing={null}
            initialValues={managedFormInitial}
            onClose={onCloseManagedForm}
            onSaved={onManagedFormSaved}
          />
          <ManagedNeConnectDetailDialog
            row={connectDetailRow}
            onClose={onCloseConnectDetail}
            onRetestSubmitted={onConnectRetestSubmitted}
          />
          <OutsidePeersDialog
            open={outsidePeersOpen}
            outsidePeers={outsidePeers}
            outsidePeersVisible={outsidePeersVisible}
            outsidePeerQuery={outsidePeerQuery}
            onOutsidePeerQueryChange={onOutsidePeerQueryChange}
            outsidePeerSelectedIds={outsidePeerSelectedIds}
            onOutsidePeerSelectedIdsChange={onOutsidePeerSelectedIdsChange}
            outsidePeerNameById={outsidePeerNameById}
            outsidePeersAdding={outsidePeersAdding}
            onClose={onCloseOutsidePeers}
            onAddOutsidePeers={onAddOutsidePeers}
          />
          <AddNePaletteDialog
            open={addNeOpen}
            paletteSource={paletteSource}
            onPaletteSourceChange={onPaletteSourceChange}
            keyword={keyword}
            onKeywordChange={onKeywordChange}
            paletteVisible={paletteVisible}
            paletteSelectedKeys={paletteSelectedKeys}
            onPaletteSelectedKeysChange={onPaletteSelectedKeysChange}
            paletteLoading={paletteLoading}
            paletteAdding={paletteAdding}
            onClose={onCloseAddNe}
            onPaletteDragStart={onPaletteDragStart}
            onAddSelected={onAddSelectedPalette}
          />
        </>
      ) : null}
    </>
  );
}
