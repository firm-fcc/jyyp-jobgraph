/* ============================================================
   数据清单的单独入口

   `manifest.json` 不足两千字节，却带着封面页要报的全部规模数、观测区间与
   体系版本。把它从 graphData 里单列出来，封面页因而只等这一份小文件即可成屏，
   十余兆的图谱产物留到进入系统时再取（见 App.tsx 的分块与预取）。

   graphData 也从这里取清单，故全站只有一处读它，不会取回两遍。
   ============================================================ */

import type { GraphManifest } from './graphData';

const BASE = import.meta.env.BASE_URL || '/';

const r = await fetch(`${BASE}data/manifest.json`);
if (!r.ok) throw new Error(`数据清单加载失败：${r.status}`);

export const MANIFEST = (await r.json()) as GraphManifest;

/** 观测窗口，升序 */
export const WINDOWS = MANIFEST.windows;
/** 末窗，图谱结构以它为准 */
export const LATEST_WINDOW = MANIFEST.latest;
