import { useId } from "react";

/** NETX mark: an "X" of links, four nodes around a hub; one node lit as "live". */
export function BrandMark({ size = 24, className }: { size?: number; className?: string }) {
  const gid = useId();
  return (
    <svg className={className} viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#3b82f6" />
          <stop offset="1" stopColor="#1d4ed8" />
        </linearGradient>
      </defs>
      <rect width="24" height="24" rx="6.5" fill={`url(#${gid})`} />
      <path d="M7 7l10 10M17 7 7 17" stroke="#fff" strokeOpacity=".55" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="12" r="2.6" fill="#fff" />
      <circle cx="7" cy="7" r="1.9" fill="#fff" />
      <circle cx="7" cy="17" r="1.9" fill="#fff" />
      <circle cx="17" cy="17" r="1.9" fill="#fff" />
      <circle cx="17" cy="7" r="2.1" fill="#67e8f9" />
    </svg>
  );
}
