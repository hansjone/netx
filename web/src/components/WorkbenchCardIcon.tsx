import type { ModuleIconKind, ModuleIconTone } from "../config/modules";

type Props = {
  tone: ModuleIconTone;
  kind: ModuleIconKind;
};

function IconPath({ kind }: { kind: ModuleIconKind }) {
  switch (kind) {
    case "sync":
      return (
        <path d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46A7.93 7.93 0 0 0 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74A7.93 7.93 0 0 0 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z" />
      );
    case "server":
      return (
        <path d="M4 4h16a1 1 0 0 1 1 1v5H3V5a1 1 0 0 1 1-1zm-1 8h18v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5zm3 1.25a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5zm0-8a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5zM8 6.5h6v1.25H8V6.5zm0 8h6v1.25H8V14.5z" />
      );
    case "network":
      return (
        <path d="M12 2a3 3 0 0 1 1 5.83V10h3a3 3 0 1 1 0 2h-3v2.17A3.001 3.001 0 1 1 11 17.83V14H8a3 3 0 1 1 0-2h3v-2.17A3.001 3.001 0 0 1 12 2zm0 2a1 1 0 1 0 .001 2.001A1 1 0 0 0 12 4zM6 11a1 1 0 1 0 0 2 1 1 0 0 0 0-2zm12 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2zm-6 6a1 1 0 1 0 0 2 1 1 0 0 0 0-2z" />
      );
    case "topology":
      return (
        <path d="M7 3a3 3 0 0 1 2.83 4H11v2h2V7h1.17A3.001 3.001 0 1 1 17 10.83V12h2v2h-2v1.17A3.001 3.001 0 1 1 13.17 18H12v-2h-1.17A3.001 3.001 0 1 1 7 13.17V12H5v-2h2V8.83A3.001 3.001 0 0 1 7 3zm10 2a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM7 5a1 1 0 1 0 0 2 1 1 0 0 0 0-2zm0 12a1 1 0 1 0 0 2 1 1 0 0 0 0-2zm10 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2z" />
      );
    case "terminal":
      return (
        <path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5zm2 0v14h12V5H6zm2.3 3.2 1.4-1.4L13.1 10l-3.4 3.2-1.4-1.4L10.3 10 8.3 8.2zM12 15h4v1.8h-4V15z" />
      );
    case "wall":
      return (
        <path d="M3 5h8v6H3V5zm10 0h8v6h-8V5zM3 13h8v6H3v-6zm10 0h8v6h-8v-6zm2-6v2h4V7h-4zm0 8v2h4v-2h-4zM5 7v2h4V7H5zm0 8v2h4v-2H5z" />
      );
    case "users":
      return (
        <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-3.33 0-6 1.34-6 3v1h12v-1c0-1.66-2.67-3-6-3zm7.5-5.5a2.5 2.5 0 1 0-2.45-3H17a2.5 2.5 0 0 0 2.5 3.5zM19 12c1.66 0 3 .67 3 1.5V14h-2.1c-.3-.7-1-1.2-1.9-1.5v-.5z" />
      );
    case "audit":
      return (
        <path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm7 1.5V9h4.5L14 4.5zM9 12h6v1.5H9V12zm0 3.5h6V17H9v-1.5zm0-7h3V10H9V8.5z" />
      );
    case "key":
      return (
        <path d="M12.65 10A5.99 5.99 0 0 0 7 4a6 6 0 0 0-1 11.92V20l2 2 2-2 1-1v-2.08c.33.05.66.08 1 .08a6 6 0 0 0 5.65-8H12.65zM7 8a2 2 0 1 1 0 4 2 2 0 0 1 0-4z" />
      );
    default:
      return (
        <path d="M12 2a2 2 0 0 1 2 2v1.07A7.002 7.002 0 0 1 19.93 11H21a2 2 0 0 1 0 4h-1.07A7.002 7.002 0 0 1 13 20.93V22a2 2 0 0 1-4 0v-1.07A7.002 7.002 0 0 1 4.07 15H3a2 2 0 0 1 0-4h1.07A7.002 7.002 0 0 1 11 5.07V4a2 2 0 0 1 2-2zm0 6a4 4 0 1 0 0 8 4 4 0 0 0 0-8z" />
      );
  }
}

export function WorkbenchCardIcon({ tone, kind }: Props) {
  return (
    <span className={`wb-card__icon wb-card__icon--${tone}`} aria-hidden>
      <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
        <IconPath kind={kind} />
      </svg>
    </span>
  );
}
