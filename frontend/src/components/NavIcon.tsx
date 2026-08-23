/** Rail icons, drawn inline rather than pulled from an icon package.
 *
 * Eight 24px stroke paths is less weight than a dependency, and it keeps the
 * set consistent -- one stroke width, one corner treatment, one grid. They
 * inherit `currentColor`, so the rail's own colour rules drive them. */

const PATHS: Record<string, React.ReactNode> = {
  photo: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="M3 16l5-4 4 3 3-2 6 5" />
    </>
  ),
  video: (
    <>
      <rect x="3" y="6" width="13" height="12" rx="2" />
      <path d="M16 10l5-3v10l-5-3z" />
    </>
  ),
  sparkle: (
    <>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
      <circle cx="12" cy="12" r="4.5" />
    </>
  ),
  music: (
    <>
      <path d="M9 18V6l10-2v12" />
      <circle cx="7" cy="18" r="2" />
      <circle cx="17" cy="16" r="2" />
    </>
  ),
  waveform: <path d="M4 10v4M8 7v10M12 5v14M16 8v8M20 11v2" />,
  layers: (
    <>
      <path d="M3 8l9-4 9 4-9 4-9-4z" />
      <path d="M3 13l9 4 9-4" />
    </>
  ),
  grid: (
    <>
      <rect x="3" y="4" width="7" height="7" rx="1.5" />
      <rect x="14" y="4" width="7" height="7" rx="1.5" />
      <rect x="3" y="15" width="7" height="5" rx="1.5" />
      <rect x="14" y="15" width="7" height="5" rx="1.5" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 00-.1-1l2-1.5-2-3.4-2.3 1a7 7 0 00-1.7-1L14.5 3h-4l-.4 2.5a7 7 0 00-1.7 1l-2.3-1-2 3.4L6 11a7 7 0 000 2l-2 1.5 2 3.4 2.3-1a7 7 0 001.7 1l.4 2.6h4l.4-2.6a7 7 0 001.7-1l2.3 1 2-3.4-2-1.5a7 7 0 00.2-1z" />
    </>
  ),
  logo: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 14l4.5-4 4 3.5L16 9l5 4.5" />
    </>
  ),
  server: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
    </>
  ),
  cloud: <path d="M17.5 19a4.5 4.5 0 000-9 6 6 0 00-11.6 1.6A4 4 0 006.5 19z" />,
};

export function NavIcon({ name, size = 16 }: { name: string; size?: number }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {path}
    </svg>
  );
}
