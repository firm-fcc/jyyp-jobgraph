/* 全局搜索索引 —— 直接由图谱节点表生成，
   所以搜索结果里的每一条都能真的落到对应页面的那个条目上。
   结构沿用初版前端 app.js 的 searchIndex()。 */

import type { GraphNode } from '@/types/graph';

export interface SearchItem {
  label: string;
  type: '新岗位' | '岗位' | '任务' | '能力' | '技能点';
  to: string;
}

const TYPE_OF: Record<GraphNode['kind'], SearchItem['type']> = {
  job: '岗位',
  task: '任务',
  skill: '能力',
  skillpoint: '技能点',
};

/** 岗位落到岗位洞察页对应子页面；其余落到全景图谱并选中 */
export function buildSearchIndex(nodes: GraphNode[]): SearchItem[] {
  return nodes.map((n) => ({
    label: n.name,
    type: n.kind === 'job' && n.emerging ? '新岗位' : TYPE_OF[n.kind],
    to:
      n.kind === 'job'
        ? `/jobs?tab=${n.emerging ? 'new' : 'existing'}&id=${encodeURIComponent(n.id)}`
        : `/panorama?focus=${encodeURIComponent(n.id)}`,
  }));
}
