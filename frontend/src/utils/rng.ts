/** 确定性伪随机 —— 保证每次刷新数据完全一致，演示可复现 */

export function hashStr(s: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 由任意字符串键派生一个稳定的 [0,1) 值 */
export function rand01(key: string): number {
  return mulberry32(hashStr(key))();
}

/** 由键派生 [lo, hi) 内的稳定值 */
export function randRange(key: string, lo: number, hi: number): number {
  return lo + rand01(key) * (hi - lo);
}

export function randInt(key: string, lo: number, hi: number): number {
  return Math.floor(randRange(key, lo, hi + 1 - 1e-9));
}

export function pick<T>(key: string, arr: readonly T[]): T {
  return arr[Math.min(arr.length - 1, Math.floor(rand01(key) * arr.length))];
}

export function pickMany<T>(key: string, arr: readonly T[], n: number): T[] {
  const scored = arr.map((v, i) => ({ v, s: rand01(`${key}#${i}`) }));
  scored.sort((a, b) => a.s - b.s);
  return scored.slice(0, Math.min(n, arr.length)).map((x) => x.v);
}

export const clamp = (v: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));

/** 逻辑斯蒂曲线：用于生成实体强度的自然增长形态 */
export function logistic(x: number, mid: number, steep: number) {
  return 1 / (1 + Math.exp(-(x - mid) / steep));
}
