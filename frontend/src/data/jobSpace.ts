/* ============================================================
   岗位空间 —— 新发现的岗位落在已有岗位体系的什么位置

   相图回答的是“这个候选岗位是不是真的在长出来”，两根轴都是时间与强度，
   没有一根说得清“它离哪个已有岗位近”。这张图补的正是这一维：
   把全部岗位放进同一个任务向量空间，投影到二维，
   于是“新簇贴着哪个已有岗位圈、又差多远”成了可以量的东西。

   ------------------------------------------------------------
   坐标怎么来

   ① 向量：每个岗位取它的任务构成（J-T 边的有效权重），L2 归一化。
      此处早前另有一档“任务 + 能力”的坐标依据，供界面上切换。两档的落位并不相同，
      读图前须先判断当前这张图按哪一档画出，而算法侧的图形构建只保留一种口径，
      故切换一并撤除，只留任务这一档。
   ② 投影：多维标度（MDS）。目标函数直接就是这张图要成立的那句话 ——
      让图上两点的距离尽量等于它们的余弦距离。先用主成分分析给一个初值，
      再跑 SMACOF 迭代收敛。
   ③ 两根轴共用一个标尺。MDS 的解本来就只到相似变换为止（旋转、平移、整体缩放），
      任何一边被单独拉伸，“距离”就不再是距离 —— 这张图的全部结论都建立在距离上。

   ------------------------------------------------------------
   为什么不是只做主成分分析

   做过：11 个新岗位会挤成一坨。主成分取的是全体 266 个岗位方差最大的两个方向，
   而新岗位彼此的差别（编排 / 检索 / 评测 / 边缘）落在方差小得多的方向上，
   投到前两维里就被压没了。这张图偏偏要读的就是那点差别，所以换成 MDS：
   它不挑方向，它直接压低“图上距离与真实距离之差”。代价是有残差，
   残差用 Kruskal 应力（stress）报出来，见 stress 字段。

   ------------------------------------------------------------
   为什么报数不用图上的距离

   二维还原不可能没有残差。因此邻近关系一律回到原始向量上算余弦距离：
   图上读方位，报数报高维。拿投影后的像素距离当结论，
   等于把残差也一起算进读数里。

   ------------------------------------------------------------
   被投影的那份关系

   J-T 边取自算法侧的实测产出，权重为招聘信息的统计量。上一版这类边由演示
   补齐层按岗位类别生成，图因而是“补出来的那份关系”的真实投影；接入图谱产物后
   投影与被投影的关系两侧都是实测，图上的距离读数可直接回到岗位的任务构成核对。

   叠层岗位是例外中的一半：它们的边同样实测，但只有参与了岗位关联边的那几个
   才有落点，其余的任务构成来自与之关联的单条边，投影位置的稳健性弱于既有岗位。

   ------------------------------------------------------------
   叠层新岗位的任务构成从哪儿来

   算法侧没有产出这一层：叠层新岗位在四类边里一条也没有。算法侧的叠层关联边
   （delta/job_links.json，四十四个叠层窗合计 299 条、末窗 1 条）落在 delta 层，
   不进 effective 的 J-T 表；即便把末窗那一条计进来，六十项任务里也只有一项
   非零，距离由单一分量定，同样读不出落位。任务向量因而全是零向量。

   零向量与任一单位向量的内积为零，余弦距离恒等于 1；两个零向量之间同样是 1。
   若照常排序取前三，得到的是三个并列 1.000 的“相近岗位”，且对照清单里
   新岗位一侧整列为空。1.000 在这张图上的既定读法是“任务构成完全不相干”，
   而此处的实情是“没有任务构成可比”—— 把后者报成前者，是拿一个代数恒等式
   冒充一次测量。这几个点因此一直读不出落位。

   构建阶段现由两处已有字段推得一份（data-pipeline/jobvec.mjs）：一是 JD 类
   证据句里算法侧已抽出、但没有写成边的技能与任务名，二是定义与证据句同
   任务锚点文本的相似度。推得的向量与既有岗位的实测向量落在同一空间，
   距离因而算得出来，但一侧是推导，由 inferred 逐点标出，界面上一并报出。

   推不出来的仍不给清单：证据太少时（只有一两句英文摘录），锚点相似度落在
   噪声量级上，排出来的前几项是噪声的次序，不是这个岗位的构成。这类点仍由
   grounded 标出，点画作虚线空心，右栏与图注各自交代口径。

   两类点都参与投影：排除出去，其余岗位的相对位置反而会随之改变，
   而它们被 MDS 推到点云外围本身即是“与谁都不重合”的忠实解。
   ============================================================ */

import type { EntitySignal, EvidenceRef, GraphEdge, GraphNode } from '@/types/graph';
import { MONTHS } from './generator';

/** 对照表最多列出的任务条数。列全了卡片要翻两屏，读者只会看前几条 */
const COMPARE_TOP = 6;

/** 一个已有岗位圈至少要有这么多成员才画得出“圈”，少于此只留点 */
const RING_MIN = 5;

/** 圈半径取成员到质心距离的这一分位：圈内含该大类四分之三的岗位 */
const RING_QUANTILE = 0.75;

/** 对照里的一项任务。两个占比皆为该任务在各自岗位任务构成中所占的份额 */
export interface SpaceTaskItem {
  id: string;
  name: string;
  /** 在新岗位任务构成中的占比。该岗位不承担此任务时为 0 */
  a: number;
  /** 在对照的已有岗位中的占比 */
  b: number;
}

/**
 * 新岗位与某个已有岗位的对照。
 *
 * 距离只给出“有多远”，不给出“差在哪儿”。后者要落到任务这一层。
 *
 * 差别有两种形态，缺一不可：一是任务集合不同（一侧承担、另一侧不承担），
 * 二是集合相同而份额不同。只报集合差在当前这批关系上会落空 ——
 * 最近的那个已有岗位往往与新岗位承担同一组任务，差别整个落在份额上，
 * 只列集合差就成了“两侧独有各 0 项”，等于什么也没说。
 * 故此处给出两侧任务的并集与各自份额，集合差退为三个计数。
 */
export interface SpaceNeighbor {
  id: string;
  name: string;
  cluster: string;
  /** 高维余弦距离。0 = 任务构成完全一致，1 = 完全不相干 */
  dist: number;
  /** 已有岗位的市场占比，供与新岗位并置 */
  share: number;
  /** 两侧任务的并集，按份额之和降序，至多 COMPARE_TOP 项 */
  tasks: SpaceTaskItem[];
  /** 并集的全量项数。清单被截断，计数不截断 */
  nTasks: number;
  /** 两侧都承担的项数 */
  nShared: number;
  /** 仅新岗位承担的项数 */
  nOnlyNew: number;
  /** 仅该已有岗位承担的项数 */
  nOnlyRef: number;
}

export interface JobSpacePoint {
  job: GraphNode;
  /** 投影坐标。两轴同标尺，较长的一边落在 [0, 1] */
  x: number;
  y: number;
  /**
   * 该岗位有没有任务构成可比。
   *
   * 没有的岗位，任务向量是零向量：它与体系内每一个岗位的余弦距离都恰好等于 1，
   * 与另一个同样没有边的岗位也是 1。1 在这张图上的读法是“任务构成完全不相干”，
   * 而此处的实情是“没有任务构成可比”，两者不是一回事 —— 照常列出三个
   * 距离 1.000 的相近岗位，等于把“测不出”报成了“测出来是最远”。
   * 故这类岗位不给相近岗位清单，由界面按本字段如实交代。
   */
  grounded: boolean;
  /**
   * 任务构成是推导来的，不是实测的。
   *
   * 叠层新岗位在四类边里一条也没有 —— 算法侧的 delta/job_links.json 四十四个
   * 叠层窗合计 299 条、末窗 1 条，且落在 delta 层不进 effective。构建阶段
   * 由两处已有字段推得一份：JD 类证据句里
   * 算法侧已抽出、但没有写成边的技能与任务名（读数），以及定义与证据句同
   * 任务锚点文本的相似度（推断）。见 data-pipeline/jobvec.mjs。
   *
   * 推得的向量与既有岗位的实测向量落在同一空间，余弦距离因而算得出来，
   * 但一侧是推导。界面上凡报这类岗位的距离，一律同时报出这一条。
   */
  inferred: boolean;
  /** 市场占比（最新一期截面） */
  share: number;
  /** 前瞻信号构成：论文与新闻各占多少，两者和为 1；两者皆无时同为 0 */
  paperShare: number;
  newsShare: number;
  /** 前瞻信号绝对强度（论文 + 新闻），决定颜色浓度 */
  foresight: number;
  /** 最近的已有岗位，最多三个。仅新岗位有值 */
  near: SpaceNeighbor[];
  /** 证据首现 / 末现 */
  firstAt: string;
  lastAt: string;
  firstPaperAt?: string;
  firstNewsAt?: string;
  /** 论文与新闻各取最近的一条，作为悬停时的出处 */
  cite: EvidenceRef[];
}

export interface JobSpaceRing {
  name: string;
  cx: number;
  cy: number;
  r: number;
  count: number;
  /** 是否为某个新岗位最近邻所在的大类 —— 只有这些圈值得标名字 */
  adjacent: boolean;
}

export interface JobSpace {
  points: JobSpacePoint[];
  rings: JobSpaceRing[];
  /** 新岗位总数，与下两项并读即得“几个里有几个定得住、其中几个是推的” */
  emergingCount: number;
  /** 其中任务向量为零、无从计算相近岗位的个数 */
  ungroundedCount: number;
  /** 其中任务构成由推导得来的个数 */
  inferredCount: number;
  /** 参与投影的维数 */
  dims: number;
  /** Kruskal 应力，二维还原与真实距离的偏离程度，越小越贴合 */
  stress: number;
  /** 两轴的实际跨度，较长的一边为 1 —— 落位时按它保持等比 */
  spanX: number;
  spanY: number;
  maxShare: number;
  /**
   * 新岗位里新闻占比的实际区间 [最低, 最高]。
   *
   * 双色色标按这个区间定标，而不是按理论上的 0 ~ 100%。补齐层给出的论文与新闻
   * 两条曲线，比值集中在四十个百分点以内的一段窄区间里 —— 按 0 ~ 100% 上色，
   * 11 个点会调出同一种颜色，这一维就白画了。
   * 弧长仍按原始占比画，读数也仍报原始占比：定标只影响颜色的可比性，不改数。
   */
  leanRange: [number, number];
}

/* ==================== 线性代数 ==================== */

function unit(v: Float64Array, from: number, to: number): number {
  let s = 0;
  for (let i = from; i < to; i++) s += v[i] * v[i];
  const n = Math.sqrt(s);
  if (n > 1e-12) for (let i = from; i < to; i++) v[i] /= n;
  return n;
}

/**
 * 幂迭代求最大特征向量。
 *
 * 维数只有三十几，整个矩阵不到两千个数，迭代几百轮的开销可以忽略；
 * 引一个矩阵库反而要把一份几十 KB 的依赖搬进包里。
 * 起始向量由下标定死，不用随机数 —— 这张图必须每次刷新都一模一样。
 */
function topEigen(C: Float64Array, D: number, phase: number): { vec: Float64Array; val: number } {
  let v = new Float64Array(D);
  for (let i = 0; i < D; i++) v[i] = Math.cos((i + 1) * phase) + 0.37;
  unit(v, 0, D);

  let val = 0;
  for (let it = 0; it < 260; it++) {
    const w = new Float64Array(D);
    for (let i = 0; i < D; i++) {
      let s = 0;
      const row = i * D;
      for (let k = 0; k < D; k++) s += C[row + k] * v[k];
      w[i] = s;
    }
    const m = unit(w, 0, D);
    if (m < 1e-12) break;
    let drift = 0;
    for (let i = 0; i < D; i++) drift += Math.abs(w[i] - v[i]);
    val = m;
    v = w;
    if (drift < 1e-11) break;
  }

  /* 定符号：分量绝对值最大的那一维取正。否则同一份数据在两次调用间可能整体翻面，
     数据一更新整张图就左右镜像一下，看着像换了一批岗位。 */
  let mi = 0;
  for (let i = 1; i < D; i++) if (Math.abs(v[i]) > Math.abs(v[mi])) mi = i;
  if (v[mi] < 0) for (let i = 0; i < D; i++) v[i] = -v[i];

  return { vec: v, val };
}

/** 前两个主成分。只用来给 MDS 一个确定性的初值 */
function pca2(vs: Float64Array[], D: number) {
  const n = vs.length;
  const mean = new Float64Array(D);
  for (const v of vs) for (let i = 0; i < D; i++) mean[i] += v[i];
  for (let i = 0; i < D; i++) mean[i] /= n;

  const C = new Float64Array(D * D);
  const c = new Float64Array(D);
  for (const v of vs) {
    for (let i = 0; i < D; i++) c[i] = v[i] - mean[i];
    for (let i = 0; i < D; i++) {
      const a = c[i];
      if (a === 0) continue;
      const row = i * D;
      for (let k = i; k < D; k++) C[row + k] += a * c[k];
    }
  }
  let trace = 0;
  for (let i = 0; i < D; i++) {
    for (let k = i; k < D; k++) {
      const v = C[i * D + k] / n;
      C[i * D + k] = v;
      C[k * D + i] = v;
    }
    trace += C[i * D + i];
  }

  const e1 = topEigen(C, D, 1.7);
  // 抽掉第一主成分再求一次，得到的就是与它正交的第二主成分
  for (let i = 0; i < D; i++)
    for (let k = 0; k < D; k++) C[i * D + k] -= e1.val * e1.vec[i] * e1.vec[k];
  const e2 = topEigen(C, D, 2.9);

  return { mean, a1: e1.vec, a2: e2.vec };
}

/* ==================== 多维标度 ==================== */

/** SMACOF 的迭代上限与收敛判据 */
const MDS_ITERS = 160;
const MDS_TOL = 2e-6;
/** 每隔几轮验一次应力。每轮都验要多跑一遍 n²/2 的开方，收敛判据不值这个价 */
const MDS_CHECK = 4;

/**
 * SMACOF —— 让二维距离尽量等于给定的距离矩阵。
 *
 * 权重取一律相等，用的是 Guttman 变换在等权下的化简式：
 *   X⁺ᵢ = (1/n) · Σⱼ≠ᵢ (δᵢⱼ / dᵢⱼ(X)) · (Xᵢ − Xⱼ)
 * 这一式自带居中（求和反对称），所以迭代过程中坐标始终以原点为心，
 * 不必每轮再减一次均值。
 *
 * 初值来自主成分投影而不是随机撒点：这张图每次刷新必须一模一样，
 * 而 SMACOF 只保证收敛到局部极小，初值一变结果就变。
 */
function smacof(dis: Float32Array, n: number, init: Float64Array): { X: Float64Array; stress: number } {
  /* 两个数组轮流当读写面。每轮新开一个 Float64Array 的写法在 160 轮下
     要向垃圾回收扔掉一百多个数组，光这一项就够拖出一次可感知的卡顿。 */
  let X = Float64Array.from(init);
  let Y = new Float64Array(n * 2);

  const stressOf = (P: Float64Array) => {
    let num = 0;
    let den = 0;
    for (let i = 0; i < n; i++) {
      const xi = P[i * 2];
      const yi = P[i * 2 + 1];
      const row = i * n;
      for (let j = i + 1; j < n; j++) {
        const dx = xi - P[j * 2];
        const dy = yi - P[j * 2 + 1];
        // 一律 sqrt，不用 Math.hypot：后者为防溢出多做两趟缩放，这里的量级用不上
        const d = Math.sqrt(dx * dx + dy * dy);
        const t = d - dis[row + j];
        num += t * t;
        den += d * d;
      }
    }
    return den > 1e-12 ? Math.sqrt(num / den) : 0;
  };

  let prev = stressOf(X);
  for (let it = 0; it < MDS_ITERS; it++) {
    for (let i = 0; i < n; i++) {
      let sx = 0;
      let sy = 0;
      const xi = X[i * 2];
      const yi = X[i * 2 + 1];
      const row = i * n;
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const dx = xi - X[j * 2];
        const dy = yi - X[j * 2 + 1];
        const d = Math.sqrt(dx * dx + dy * dy);
        // 两点恰好重合时该项无定义，跳过 —— 下一轮别的项会把它们推开
        if (d < 1e-9) continue;
        const k = dis[row + j] / d;
        sx += k * dx;
        sy += k * dy;
      }
      Y[i * 2] = sx / n;
      Y[i * 2 + 1] = sy / n;
    }
    const t = X;
    X = Y;
    Y = t;

    if (it % MDS_CHECK === MDS_CHECK - 1) {
      const cur = stressOf(X);
      const done = prev - cur < MDS_TOL * MDS_CHECK;
      prev = cur;
      if (done) break;
    }
  }
  return { X, stress: stressOf(X) };
}

/**
 * 把二维解转到主轴上。
 *
 * MDS 的解只定到旋转为止，转到哪个角度都一样对。既然一样对，就转到最宽的方向
 * 与横轴对齐 —— 面板是横的，不对齐就要按较短的那一边定标尺，图白白缩一圈。
 * 2×2 的主轴有闭式解，不必再迭代。
 */
function alignAxes(X: Float64Array, n: number) {
  let a = 0;
  let b = 0;
  let c = 0;
  for (let i = 0; i < n; i++) {
    a += X[i * 2] * X[i * 2];
    b += X[i * 2] * X[i * 2 + 1];
    c += X[i * 2 + 1] * X[i * 2 + 1];
  }
  const t = 0.5 * Math.atan2(2 * b, a - c);
  const cs = Math.cos(-t);
  const sn = Math.sin(-t);
  for (let i = 0; i < n; i++) {
    const x = X[i * 2];
    const y = X[i * 2 + 1];
    X[i * 2] = x * cs - y * sn;
    X[i * 2 + 1] = x * sn + y * cs;
  }
  /* 定朝向：横轴上偏度为负就整体翻面。旋转角差 180° 的两个解同样是最优解，
     不定死的话数据一更新整张图就翻个个儿，看着像换了一批岗位。 */
  let skew = 0;
  for (let i = 0; i < n; i++) skew += X[i * 2] ** 3;
  if (skew < 0) for (let i = 0; i < n; i++) X[i * 2] = -X[i * 2];
}

/* ==================== 主过程 ==================== */

export function buildJobSpace(
  jobs: GraphNode[],
  edges: GraphEdge[],
  signalMap: Map<string, EntitySignal>,
  /** 任务节点的名称查询。对照清单要列任务名，而本模块只拿得到 id */
  nameOf: (id: string) => string,
  /**
   * 叠层新岗位的推导 J-T 边（data/realGraph.ts 的 INFERRED_EDGES）。
   *
   * 算法侧没有产出这一层，缺席时这几个点仍是零向量、仍不给相近岗位清单，
   * 与接入前的形态一致。
   */
  inferredEdges: GraphEdge[] = [],
): JobSpace {
  const last = MONTHS.length - 1;

  /* ---- 维度表 ----
     维度只由实测边定：推导边的终点若落在实测边未覆盖的任务上，多出来的那一维
     对全部既有岗位恒为零，只有这一个新岗位在上面有值 —— 那一维于是把它与所有人
     的距离一并推高，推高的量还取决于推导本身。维度表因此不收推导边的新终点，
     落在表外的推导边在下面按维查不到，自然不计入。 */
  const taskDim = new Map<string, number>();
  for (const e of edges) {
    if (e.kind === 'J-T' && !taskDim.has(e.target)) taskDim.set(e.target, taskDim.size);
  }
  const D = taskDim.size;
  /** 反查：第 d 维是哪个任务。组装对照清单时按维回名 */
  const dimTask: string[] = new Array(D);
  for (const [id, d] of taskDim) dimTask[d] = id;

  /* ---- 岗位向量 ---- */
  const vecs = new Map<string, Float64Array>();
  for (const j of jobs) vecs.set(j.id, new Float64Array(D));
  /** 该岗位的任务构成是推导来的，不是实测的 —— 报数时逐处交代 */
  const inferredJobs = new Set<string>();
  for (const e of [...edges, ...inferredEdges]) {
    if (e.kind !== 'J-T') continue;
    const v = vecs.get(e.source);
    if (!v) continue;
    const d = taskDim.get(e.target);
    if (d === undefined) continue;
    v[d] += e.effectiveWeight;
    if (e.provenance === 'derived') inferredJobs.add(e.source);
  }

  /* 归一化前的模长留着：它为零即该岗位在本批数据里没有任何任务关联边，
     下面的距离与对照两处都要按这一条分流。unit 恰好返回归一化前的模长。 */
  const norms = new Map<string, number>();
  for (const [id, v] of vecs) norms.set(id, unit(v, 0, D));
  const grounded = (id: string) => (norms.get(id) ?? 0) > 1e-12;

  /* ---- 距离矩阵 ----
     向量都在单位球面上，所以余弦距离直接就是 1 − 内积。 */
  const order = jobs.filter((j) => vecs.has(j.id));
  const n = order.length;
  const mat = order.map((j) => vecs.get(j.id)!);
  const dis = new Float32Array(n * n);
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      let dot = 0;
      const a = mat[i];
      const b = mat[j];
      for (let k = 0; k < D; k++) dot += a[k] * b[k];
      const d = Math.max(0, 1 - dot);
      dis[i * n + j] = d;
      dis[j * n + i] = d;
    }
  }

  /* ---- 初值：主成分投影 ---- */
  const { mean, a1, a2 } = pca2(mat, D);
  const init = new Float64Array(n * 2);
  for (let i = 0; i < n; i++) {
    let x = 0;
    let y = 0;
    const v = mat[i];
    for (let k = 0; k < D; k++) {
      const c = v[k] - mean[k];
      x += c * a1[k];
      y += c * a2[k];
    }
    /* 加一个由下标定死的微小偏移：主成分投影会把任务构成完全相同的岗位
       压到同一个点上，两点重合时 Guttman 变换那一项无定义，永远推不开。 */
    init[i * 2] = x + Math.cos(i * 2.399) * 1e-4;
    init[i * 2 + 1] = y + Math.sin(i * 2.399) * 1e-4;
  }
  /* 初值先缩放到与距离矩阵同一量级。差一个数量级时 SMACOF 也收敛，
     只是要多跑几十轮才把整体尺度调回来。 */
  {
    let sd = 0;
    let sp = 0;
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        sd += dis[i * n + j];
        const dx = init[i * 2] - init[j * 2];
        const dy = init[i * 2 + 1] - init[j * 2 + 1];
        sp += Math.sqrt(dx * dx + dy * dy);
      }
    const k = sp > 1e-12 ? sd / sp : 1;
    for (let i = 0; i < n * 2; i++) init[i] *= k;
  }

  const { X, stress } = smacof(dis, n, init);
  alignAxes(X, n);

  const raw = order.map((j, i) => ({ job: j, x: X[i * 2], y: X[i * 2 + 1] }));
  const xs = raw.map((p) => p.x);
  const ys = raw.map((p) => p.y);
  const x0 = Math.min(...xs);
  const y0 = Math.min(...ys);
  const rx = Math.max(...xs) - x0 || 1;
  const ry = Math.max(...ys) - y0 || 1;
  const scale = 1 / Math.max(rx, ry);

  /* ---- 邻近关系 ----
     回到高维算，用的就是上面那张距离矩阵，不读二维坐标。 */
  const indexOf = new Map(order.map((j, i) => [j.id, i]));
  const settled = order.filter((j) => !j.emerging);

  /* ---- 任务构成对照 ----
     份额按 L1 口径给出：向量已做 L2 归一化，而 L2 归一化在任务这一段内是等比缩放，
     再除以段内之和即还原为原始权重的份额，读数因此仍是“这项任务占几成”。
     每个岗位只算一次，一个新岗位要与三个已有岗位对照，不缓存即重复三遍。 */
  const shareCache = new Map<string, Map<string, number>>();
  const sharesOf = (id: string): Map<string, number> => {
    const hit = shareCache.get(id);
    if (hit) return hit;
    const v = vecs.get(id);
    const out = new Map<string, number>();
    if (v) {
      let sum = 0;
      for (let d = 0; d < D; d++) sum += v[d];
      if (sum > 1e-12) for (let d = 0; d < D; d++) if (v[d] > 1e-9) out.set(dimTask[d], v[d] / sum);
    }
    shareCache.set(id, out);
    return out;
  };

  /** 取两个岗位任务的并集，逐项给出两侧份额，并数出集合差 */
  const compare = (newId: string, refId: string) => {
    const A = sharesOf(newId);
    const B = sharesOf(refId);
    const all: SpaceTaskItem[] = [];
    let nShared = 0;
    let nOnlyNew = 0;
    let nOnlyRef = 0;
    for (const [t, a] of A) {
      const b = B.get(t) ?? 0;
      if (b > 0) nShared++;
      else nOnlyNew++;
      all.push({ id: t, name: nameOf(t), a, b });
    }
    for (const [t, b] of B) {
      if (A.has(t)) continue;
      nOnlyRef++;
      all.push({ id: t, name: nameOf(t), a: 0, b });
    }
    // 按两侧份额之和排：份额高的那几项决定了这两个岗位像不像，
    // 截断时先掉的应当是两侧都只占一点点的那些
    all.sort((x, y) => y.a + y.b - (x.a + x.b));
    return {
      tasks: all.slice(0, COMPARE_TOP),
      nTasks: all.length,
      nShared,
      nOnlyNew,
      nOnlyRef,
    };
  };

  /* ---- 逐点组装 ---- */
  let maxShare = 0;
  const points: JobSpacePoint[] = raw.map(({ job, x, y }) => {
    const sig = signalMap.get(job.id);
    /* 取该岗位最后一个有前瞻读数的窗口，不取末窗。这批候选里有几个已写进岗位
       体系，算法侧自转正的那一窗起不再把它们列入叠层记录，其后各窗的论文与
       新闻强度一律为零：按末窗取，fore 为零，外环那两段弧便一段也画不出来，
       点上只剩一个缩到半径下限的内圆与它的白垫圈 —— 看着像数据缺了一块，
       而实情是这个岗位已经不再作为新岗位被观测。 */
    const li = (() => {
      for (let i = last; i >= 0; i--) {
        if ((sig?.paper[i] ?? 0) > 0 || (sig?.news[i] ?? 0) > 0) return i;
      }
      return last;
    })();
    const paper = sig?.paper[li] ?? 0;
    const news = sig?.news[li] ?? 0;
    const fore = paper + news;

    let near: SpaceNeighbor[] = [];
    if (job.emerging && grounded(job.id)) {
      const mi = indexOf.get(job.id)!;
      near = settled
        .map((o) => ({
          id: o.id,
          name: o.name,
          cluster: o.cluster ?? o.topCategory ?? o.category,
          dist: dis[mi * n + indexOf.get(o.id)!],
          share: o.marketShare,
        }))
        .sort((a, b) => a.dist - b.dist)
        .slice(0, 3)
        // 对照只对留下的三个算：131 个已有岗位全算一遍，其中 128 份没人看
        .map((o) => ({ ...o, ...compare(job.id, o.id) }));
    }

    const ev = job.emerging ? citationsOf(job.id, edges) : { cite: [] };

    maxShare = Math.max(maxShare, job.marketShare);
    return {
      job,
      x: (x - x0) * scale,
      y: (y - y0) * scale,
      grounded: grounded(job.id),
      inferred: inferredJobs.has(job.id),
      share: job.marketShare,
      paperShare: fore > 1e-9 ? paper / fore : 0,
      newsShare: fore > 1e-9 ? news / fore : 0,
      foresight: fore,
      near,
      firstAt: ev.first ?? job.firstSeen,
      lastAt: ev.last ?? job.lastConfirmed,
      firstPaperAt: sig?.firstPaperAt,
      firstNewsAt: sig?.firstNewsAt,
      cite: ev.cite,
    };
  });

  /* ---- 已有岗位圈 ---- */
  const adjacent = new Set(
    points.flatMap((p) => p.near.map((n) => n.cluster)),
  );
  const byCluster = new Map<string, JobSpacePoint[]>();
  for (const p of points) {
    if (p.job.emerging) continue;
    const c = p.job.cluster ?? p.job.topCategory ?? p.job.category;
    const list = byCluster.get(c);
    if (list) list.push(p);
    else byCluster.set(c, [p]);
  }

  const rings: JobSpaceRing[] = [];
  for (const [name, list] of byCluster) {
    if (list.length < RING_MIN) continue;
    const cx = list.reduce((a, p) => a + p.x, 0) / list.length;
    const cy = list.reduce((a, p) => a + p.y, 0) / list.length;
    const ds = list
      .map((p) => Math.sqrt((p.x - cx) ** 2 + (p.y - cy) ** 2))
      .sort((a, b) => a - b);
    const r = ds[Math.min(ds.length - 1, Math.floor(ds.length * RING_QUANTILE))];
    rings.push({ name, cx, cy, r: Math.max(r, 0.018), count: list.length, adjacent: adjacent.has(name) });
  }
  // 大圈先画，小圈压在上面，否则小圈会被整片盖住
  rings.sort((a, b) => b.r - a.r);

  const leans = points.filter((p) => p.job.emerging && p.foresight > 1e-9).map((p) => p.newsShare);
  const leanRange: [number, number] =
    leans.length > 1 ? [Math.min(...leans), Math.max(...leans)] : [0, 1];

  const emergingPts = points.filter((p) => p.job.emerging);

  return {
    points,
    rings,
    emergingCount: emergingPts.length,
    ungroundedCount: emergingPts.filter((p) => !p.grounded).length,
    inferredCount: emergingPts.filter((p) => p.inferred).length,
    dims: D,
    stress,
    leanRange,
    spanX: rx * scale,
    spanY: ry * scale,
    maxShare,
  };
}

/* ==================== 出处 ====================
   悬停要答“凭哪篇新闻、哪篇论文”，以及“这个说法什么时候起、到什么时候还在被提”。

   前者只留论文与新闻各最近的一条 —— 浮层里塞不下更多，也不必塞：
   逐条核对是“数据来源”那一块的事，这里只需要指出有出处、出处是谁。
   后者要扫完全部前瞻原文，不能只看留下的那两条：那两条都取的是最近一条，
   拿它们算首现，结果必然是“首现等于末次”。 */

function citationsOf(
  jobId: string,
  edges: GraphEdge[],
): { cite: EvidenceRef[]; first?: string; last?: string } {
  let paper: EvidenceRef | undefined;
  let news: EvidenceRef | undefined;
  let first: string | undefined;
  let last: string | undefined;
  for (const e of edges) {
    if (e.source !== jobId) continue;
    for (const ev of e.evidence) {
      if (ev.sourceType !== 'paper' && ev.sourceType !== 'news') continue;
      if (!first || ev.publishedAt < first) first = ev.publishedAt;
      if (!last || ev.publishedAt > last) last = ev.publishedAt;
      if (ev.sourceType === 'paper') {
        if (!paper || ev.publishedAt > paper.publishedAt) paper = ev;
      } else if (!news || ev.publishedAt > news.publishedAt) news = ev;
    }
  }
  return { cite: [paper, news].filter((x): x is EvidenceRef => !!x), first, last };
}
