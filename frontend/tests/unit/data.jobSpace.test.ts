import { describe, expect, it, vi } from 'vitest';

vi.mock('@/data/generator', () => ({ MONTHS: ['2026-01', '2026-02', '2026-03'] }));

import { buildJobSpace } from '@/data/jobSpace';

const jobs = [
  ...Array.from({ length: 5 }, (_, i) => ({
    id: `J:${i}`, name: `已有岗位${i}`, kind: 'job', emerging: false,
    category: i < 3 ? 'AI' : 'Data', cluster: i < 3 ? 'AI簇' : 'Data簇', marketShare: 0.05 + i * 0.01,
    firstSeen: '2025-01', lastConfirmed: '2026-03',
  })),
  { id: 'J:new', name: '新岗位', kind: 'job', emerging: true, category: 'AI', cluster: '新簇', marketShare: 0.02, firstSeen: '2026-01', lastConfirmed: '2026-03' },
] as any[];

const tasks = ['T:1', 'T:2', 'T:3'];
const edges: any[] = [];
for (let i = 0; i < 5; i++) {
  edges.push({ kind: 'J-T', source: `J:${i}`, target: tasks[i % 3], effectiveWeight: 0.9, evidence: [] });
  edges.push({ kind: 'J-T', source: `J:${i}`, target: tasks[(i + 1) % 3], effectiveWeight: 0.4, evidence: [] });
}
edges.push({
  kind: 'J-T', source: 'J:new', target: 'T:1', effectiveWeight: 0.8, evidence: [
    { sourceType: 'paper', publishedAt: '2026-01', title: 'P' },
    { sourceType: 'news', publishedAt: '2026-02', title: 'N' },
  ],
});
edges.push({ kind: 'J-T', source: 'J:new', target: 'T:2', effectiveWeight: 0.5, evidence: [] });

const signalMap = new Map([
  ['J:new', { paper: [0.1, 0.2, 0.3], news: [0.1, 0.1, 0.2], firstPaperAt: '2026-01', firstNewsAt: '2026-01' }],
]) as any;

describe('jobSpace 岗位空间', () => {
  it('构建二维岗位空间、相近岗位、任务对照、出处和整体统计', () => {
    const space = buildJobSpace(jobs as any, edges as any, signalMap, (id) => ({ 'T:1': '模型训练', 'T:2': '数据处理', 'T:3': '部署' }[id] ?? id));
    expect(space.points).toHaveLength(6);
    expect(space.dims).toBe(3);
    expect(Number.isFinite(space.stress)).toBe(true);
    expect(space.emergingCount).toBe(1);
    expect(space.ungroundedCount).toBe(0);
    expect(space.maxShare).toBeGreaterThan(0);
    const p = space.points.find((x) => x.job.id === 'J:new')!;
    expect(p.grounded).toBe(true);
    expect(p.near.length).toBeGreaterThan(0);
    expect(p.near[0].tasks.length).toBeGreaterThan(0);
    expect(p.cite).toHaveLength(2);
    expect(p.firstAt).toBe('2026-01');
    expect(p.lastAt).toBe('2026-02');
    expect([p.x, p.y, p.paperShare, p.newsShare].every(Number.isFinite)).toBe(true);
  });

  it('推导边可给新岗位补任务构成并标记 inferred', () => {
    const withoutNew = edges.filter((e) => e.source !== 'J:new');
    const inferred = [{ kind: 'J-T', source: 'J:new', target: 'T:1', effectiveWeight: 0.8, provenance: 'derived', evidence: [] }];
    const space = buildJobSpace(jobs as any, withoutNew as any, signalMap, (id) => id, inferred as any);
    const p = space.points.find((x) => x.job.id === 'J:new')!;
    expect(p.grounded).toBe(true);
    expect(p.inferred).toBe(true);
    expect(space.inferredCount).toBe(1);
  });
});
