/* ============================================================
   能力发展图谱的取数

   public/data/devgraph.json 是 backend/candidate_core/config 下那批
   能力发展图谱的精简副本（见 scripts/export-devgraph.mjs）。解析服务在线时
   学习路径由服务端规划，这一份不参与；不在线、或载入的是内置示例简历时，
   报告页据它给出同源的路径。

   九十余千字节，只在进入报告页时取一次，全站共用同一个 promise。
   取不到时返回 null，学习路径一节据此留空，不以推测填补。
   ============================================================ */

import { useEffect, useState } from 'react';
import type { DevGraphs } from '@/data/demoLive';

let cache: Promise<DevGraphs | null> | null = null;

function load(): Promise<DevGraphs | null> {
  if (!cache) {
    const base = import.meta.env.BASE_URL || '/';
    cache = fetch(`${base}data/devgraph.json`)
      .then((r) => (r.ok ? (r.json() as Promise<DevGraphs>) : null))
      .catch(() => null);
  }
  return cache;
}

export function useDevGraphs(enabled: boolean): DevGraphs | null {
  const [graphs, setGraphs] = useState<DevGraphs | null>(null);
  useEffect(() => {
    if (!enabled || graphs) return;
    let alive = true;
    load().then((g) => {
      if (alive && g) setGraphs(g);
    });
    return () => {
      alive = false;
    };
  }, [enabled, graphs]);
  return graphs;
}
