import type { ReactNode } from "react";
import { Modal, useOverlayState } from "@heroui/react";

export type AppModalShellProps = {
  open: boolean;
  onClose: () => void;
  /** When false, backdrop click / Escape won't dismiss (e.g. while saving). */
  dismissible?: boolean;
  size?: "sm" | "md" | "lg" | "cover" | "full" | "xs";
  className?: string;
  children: ReactNode;
};

/**
 * Controlled HeroUI Modal chrome for NetX dialogs.
 * Keeps call sites on open/onClose while using v3 compound Modal.
 */
export function AppModalShell({
  open,
  onClose,
  dismissible = true,
  size = "md",
  className = "",
  children,
}: AppModalShellProps) {
  const state = useOverlayState({
    isOpen: open,
    onOpenChange: (next) => {
      if (!next) onClose();
    },
  });

  return (
    <Modal state={state}>
      <Modal.Backdrop isDismissable={dismissible} className="app-heroui-modal-backdrop">
        <Modal.Container
          size={size}
          placement="center"
          className={`app-heroui-modal${className ? ` ${className}` : ""}`}
        >
          <Modal.Dialog className="app-heroui-modal__dialog">{children}</Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}

/** @deprecated Prefer AppModalShell — kept for topology imports. */
export function TopoModalShell(props: AppModalShellProps) {
  return <AppModalShell {...props} />;
}
