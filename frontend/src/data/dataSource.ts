/* ============================================================
   取数开关

   三档，由环境变量 VITE_DATA 切换：

     默认（graph）      算法侧的图谱产物：四层节点、四类边、各观测窗口的
                        月度序列、叠层前瞻信号、技能熟练度分布
     VITE_DATA=taxonomy 只用三份分类文件的体系，关系与时序由演示补齐层生成。
                        图谱产物接入之前的形态，留作对照
     VITE_DATA=mock     退回 taxonomy.ts 里的演示词表，用于规模对照

   单列一个模块而不并入 seeds.ts：演示补齐层与词表种子层互相引用，
   开关若挂在其中一侧，另一侧在模块求值期取值会落进暂时性死区。
   本模块不依赖任何其他模块，两侧都可安全读取。
   ============================================================ */

export type DataSource = 'graph' | 'taxonomy' | 'mock';

export const DATA_SOURCE: DataSource =
  import.meta.env.VITE_DATA === 'mock'
    ? 'mock'
    : import.meta.env.VITE_DATA === 'taxonomy'
      ? 'taxonomy'
      : 'graph';

/** 体系来自算法侧的三份分类文件 */
export const IS_REAL_TAXONOMY = DATA_SOURCE !== 'mock';
/** 节点、边与时序来自算法侧的图谱产物 */
export const IS_REAL_GRAPH = DATA_SOURCE === 'graph';
