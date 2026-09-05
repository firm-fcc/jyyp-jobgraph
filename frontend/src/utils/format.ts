/** 月份工具：全站以 'YYYY-MM' 为时间原语 */

export function monthIndex(m: string): number {
  const [y, mo] = m.split('-').map(Number);
  return y * 12 + (mo - 1);
}

export function indexToMonth(i: number): string {
  const y = Math.floor(i / 12);
  const mo = (i % 12) + 1;
  return `${y}-${String(mo).padStart(2, '0')}`;
}

export function monthRange(from: string, to: string): string[] {
  const a = monthIndex(from);
  const b = monthIndex(to);
  const out: string[] = [];
  for (let i = a; i <= b; i++) out.push(indexToMonth(i));
  return out;
}

export function addMonths(m: string, n: number): string {
  return indexToMonth(monthIndex(m) + n);
}

export function monthDiff(a: string, b: string): number {
  return monthIndex(b) - monthIndex(a);
}

export function shortMonth(m: string): string {
  const [y, mo] = m.split('-');
  return `${y.slice(2)}/${mo}`;
}

export const pct = (v: number, d = 0) => `${(v * 100).toFixed(d)}%`;
export const num = (v: number, d = 2) => v.toFixed(d);

export function signed(v: number, d = 2) {
  return `${v >= 0 ? '+' : ''}${v.toFixed(d)}`;
}

/** 数值截断为紧凑显示 */
export function compact(v: number): string {
  if (v >= 10000) return `${(v / 10000).toFixed(1)}w`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(Math.round(v));
}
