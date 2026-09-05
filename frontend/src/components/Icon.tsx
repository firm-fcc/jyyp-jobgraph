/* 内联图标集 —— 自初版前端 frontend/js/ui.js 的 ICONS 移植，保持全站图形语言一致 */

const ICONS: Record<string, string> = {
  graph:
    '<circle cx="6" cy="6" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="12" cy="18" r="2.6"/><path d="M7.8 7.8 11 15M16.2 7.8 13 15M8.5 6h7" stroke="currentColor" stroke-width="1.8" fill="none"/>',
  search:
    '<circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="m20 20-3.5-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  user: '<circle cx="12" cy="8" r="3.6" fill="none" stroke="currentColor" stroke-width="1.9"/><path d="M4.8 20a7.2 7.2 0 0 1 14.4 0" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
  target:
    '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="4.8" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/>',
  spark: '<path d="M12 2.5 13.8 8.7 20 10.5 13.8 12.3 12 18.5 10.2 12.3 4 10.5 10.2 8.7z" fill="currentColor"/>',
  doc: '<path d="M6 3h8l4 4v14H6z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M14 3v4h4M9 12h6M9 16h6" stroke="currentColor" stroke-width="1.8" fill="none"/>',
  shield:
    '<path d="M12 3 5 5.8v5.4c0 4.4 3 8 7 9.8 4-1.8 7-5.4 7-9.8V5.8z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m9 11.8 2.2 2.2L15.5 9.5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
  db: '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8" fill="none" stroke="currentColor" stroke-width="1.8"/>',
  trend:
    '<path d="M3 17.5 9 11l4 4 7.5-8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 7h5.5v5.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
  route:
    '<circle cx="6" cy="18.5" r="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="18" cy="5.5" r="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M8.5 18.5H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  arrowR:
    '<path d="M5 12h13m0 0-5.5-5.5M18 12l-5.5 5.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  arrowDown:
    '<path d="M12 5v13m0 0 5.5-5.5M12 18l-5.5-5.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  play: '<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>',
  layers:
    '<path d="m12 3 9 5-9 5-9-5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m4.5 12.5 7.5 4.2 7.5-4.2M4.5 16.5 12 20.7l7.5-4.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  cap: '<path d="m12 4 10 4.5L12 13 2 8.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M6.5 10.5V16c0 1.4 2.5 2.8 5.5 2.8s5.5-1.4 5.5-2.8v-5.5" fill="none" stroke="currentColor" stroke-width="1.8"/>',
  check:
    '<path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  clock:
    '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v5.4l3.4 2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  refresh:
    '<path d="M20 12a8 8 0 1 1-2.6-5.9" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M20 4v4.4h-4.4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
  edit: '<path d="M4 20h4.2L19 9.2a2.1 2.1 0 0 0-3-3L5.2 17z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m14.4 7.6 2.9 2.9" stroke="currentColor" stroke-width="1.8"/>',
  close:
    '<path d="M6.5 6.5l11 11m0-11-11 11" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/>',
  sliders:
    '<path d="M4 7h4.5M13.5 7H20M4 12h10.5M19.5 12H20M4 17h2.5M11.5 17H20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="11" cy="7" r="2.4" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="17" cy="12" r="2.4" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="9" cy="17" r="2.4" fill="none" stroke="currentColor" stroke-width="1.8"/>',
  chevronL:
    '<path d="M14.5 5.5 8 12l6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>',
  chevronR:
    '<path d="M9.5 5.5 16 12l-6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>',
  chevronD:
    '<path d="M5.5 9.5 12 16l6.5-6.5" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>',
  chevronU:
    '<path d="M5.5 14.5 12 8l6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>',
  send: '<path d="M4 11.4 20.5 4 13 20.5l-2.3-6.8z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m10.7 13.7 4.6-4.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  alert:
    '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v6.2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="16.6" r="1.15" fill="currentColor"/>',
};

export function Icon({ name, size = 16, className = '' }: { name: string; size?: number; className?: string }) {
  return (
    <svg
      className={`ic ${className}`}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: ICONS[name] ?? '' }}
    />
  );
}
