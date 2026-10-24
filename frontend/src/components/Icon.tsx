const paths = {
  issue: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 8v5m0 3h.01',
  pull: 'M6 6v12m3-15a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm0 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM15 3l-3 3 3 3m-3-3h4a3 3 0 0 1 3 3v9m3 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  brand: 'M5 7h14v13H5V7Zm7-4v4M9 12h.01M15 12h.01M9 16h6M2 11v5m20-5v5',
  plus: 'M12 5v14M5 12h14',
  project: 'M4 8h16l-2 12H6L4 8ZM3 5h18M8 2h8',
  artifact: 'M13 16a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM9 7l5-5 6 6-5 5M16 13h6v9h-6Z',
  code: 'm7 6-5 6 5 6m10-12 5 6-5 6M14 3l-4 18',
  pin: 'm9 3 10 10M11 5 5 11l8 8 6-6M4 20l5-5',
  chat: 'M4 4h16v12H9l-5 4V4Z',
  stop: 'M6 6h12v12H6Z',
  chevron: 'm8 4 8 8-8 8',
  arrow: 'M12 19V5m-6 6 6-6 6 6',
} as const;
export type IconName = keyof typeof paths;
export function Icon({ name, className = 'size-[18px]' }: { name: IconName; className?: string }) {
  return <svg className={`shrink-0 ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
