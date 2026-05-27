import type { ModuleIconTone } from "../config/modules";

type Props = {
  tone: ModuleIconTone;
};

export function WorkbenchCardIcon({ tone }: Props) {
  return (
    <span className={`wb-card__icon wb-card__icon--${tone}`} aria-hidden>
      <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
        <path d="M12 2a2 2 0 0 1 2 2v1.07A7.002 7.002 0 0 1 19.93 11H21a2 2 0 0 1 0 4h-1.07A7.002 7.002 0 0 1 13 20.93V22a2 2 0 0 1-4 0v-1.07A7.002 7.002 0 0 1 4.07 15H3a2 2 0 0 1 0-4h1.07A7.002 7.002 0 0 1 11 5.07V4a2 2 0 0 1 2-2zm0 6a4 4 0 1 0 0 8 4 4 0 0 0 0-8z" />
      </svg>
    </span>
  );
}
