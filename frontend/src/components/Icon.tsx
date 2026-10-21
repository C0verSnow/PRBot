const paths = {
  plus: 'M12 5v14M5 12h14',
  project: 'M4 8h16l-2 12H6L4 8ZM3 5h18M8 2h8',
  artifact: 'M13 16a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM9 7l5-5 6 6-5 5M16 13h6v9h-6Z',
  code: 'm7 6-5 6 5 6m10-12 5 6-5 6M14 3l-4 18',
  pin: 'm9 3 10 10M11 5 5 11l8 8 6-6M4 20l5-5',
  chat: 'M4 4h16v12H9l-5 4V4Z',
  arrow: 'M12 19V5m-6 6 6-6 6 6',
} as const;
export type IconName = keyof typeof paths;
export function Icon({ name, className = 'size-[18px]' }: { name: IconName; className?: string }) {
  return <svg className={`shrink-0 ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
