/* ============================================================
   取数适配层 —— 前端与算法后端的唯一接缝
   VITE_API_BASE 未配置时回落到确定性 Mock；配置后仅需实现七个端点：
     GET /graph      → { nodes, edges }
     GET /signals    → EntitySignal[]
     GET /prism/monthly → PrismTimeline（能力棱镜的时间维）
     GET /versions   → { versions, changes, annuli }
     GET /loop       → LoopRun[]
     GET /quality    → QualityMetrics + 治理数据
     POST /match     → MatchResult
   ============================================================ */

import { getDataset } from '@/data/generator';
import type { PrismTimeline } from '@/types/graph';

export const API_BASE = import.meta.env.VITE_API_BASE as string | undefined;
export const IS_MOCK = !API_BASE;

/** 同步取数：Mock 模式下数据在内存中，无需 loading 态 */
export function useDataset() {
  return getDataset();
}

/** 真实后端接入示例（当前未启用） */
export async function fetchGraph() {
  if (IS_MOCK) {
    const d = getDataset();
    return { nodes: d.nodes, edges: d.edges };
  }
  const r = await fetch(`${API_BASE}/graph`);
  if (!r.ok) throw new Error(`graph ${r.status}`);
  return r.json();
}

/**
 * 能力棱镜的时间维。
 *
 * 这一份是逐月的量，不是逐月的一整张图 —— 落位在前端一次算定，
 * 后端换成真实序列后棱镜不用改一行（见 PrismTimeline 的说明）。
 * provenance 一旦返回 measured，界面上那枚演示数据标即自动消失。
 */
export async function fetchPrismTimeline(): Promise<PrismTimeline> {
  if (IS_MOCK) return getDataset().prismTimeline;
  const r = await fetch(`${API_BASE}/prism/monthly`);
  if (!r.ok) throw new Error(`prism/monthly ${r.status}`);
  return r.json();
}
