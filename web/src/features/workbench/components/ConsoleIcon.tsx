type Props = { kind: "arrow" | "search" | "refresh" | "activity" | "check" | "close" | "settings" };

export function ConsoleIcon({ kind }: Props) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {kind === "arrow" && <path d="M5 12h14m-6-6 6 6-6 6" />}
      {kind === "search" && <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>}
      {kind === "refresh" && <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 11-2l3 3M4 16l3 3a7 7 0 0 0 11-2" /></>}
      {kind === "activity" && <path d="M3 12h4l3-7 4 14 3-7h4" />}
      {kind === "check" && <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>}
      {kind === "close" && <path d="m6 6 12 12M6 18 18 6" />}
      {kind === "settings" && (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.05 5.05l1.56 1.56M17.39 17.39l1.56 1.56M5.05 18.95l1.56-1.56M17.39 6.61l1.56-1.56" />
        </>
      )}
    </svg>
  );
}
