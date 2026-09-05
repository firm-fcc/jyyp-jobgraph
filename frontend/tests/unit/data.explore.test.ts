import { describe, expect, it, vi } from 'vitest';

const fx = vi.hoisted(() => {
  const skillNodes: any[] = [
    { id: 'S:T-SW-01', kind: 'skill', name: '软件与算法', category: '技术能力', topCategory: '软件与算法', skillType: 'hard', origin: 'base' },
    { id: 'S:T-DATA-01', kind: 'skill', name: '数据与计算科学', category: '技术能力', topCategory: '数据与计算科学', skillType: 'hard', origin: 'base' },
    { id: 'S:G-COMM-01', kind: 'skill', name: '沟通协作', category: '基础通用能力', topCategory: '沟通协作', skillType: 'soft', origin: 'base' },
    { id: 'S:PS-01', kind: 'skill', name: '未来叠层技能', category: '', topCategory: '', skillType: 'hard', origin: 'overlay' },
    { id: 'SP:Python', kind: 'skillpoint', name: 'Python', skillType: 'hard', definition: '编程语言' },
    { id: 'SP:SQL', kind: 'skillpoint', name: 'SQL', skillType: 'hard', definition: '查询语言' },
    { id: 'SP:Team', kind: 'skillpoint', name: '团队协作', skillType: 'soft', definition: '协同能力' },
  ];
  const jobDefs = [
    ['J:1', '算法工程师', '人工智能', 120, [0.9, 0.4, 0.2]],
    ['J:2', '机器学习工程师', '人工智能', 100, [0.8, 0.5, 0.2]],
    ['J:3', '数据工程师', '大数据', 90, [0.3, 0.9, 0.2]],
    ['J:4', '数据分析师', '大数据', 70, [0.2, 0.8, 0.4]],
    ['J:5', 'AI产品经理', '人工智能', 60, [0.3, 0.2, 0.9]],
    ['J:6', '技术顾问', '软件服务', 50, [0.4, 0.3, 0.8]],
  ] as any[];
  const jobs = jobDefs.map(([id, name, topCategory, posts, vec], i) => ({
    id, kind: 'job', name, topCategory, cluster: topCategory, category: topCategory, origin: 'base', emerging: false,
    attrs: {
      cities: i % 2 ? { 湖北: 0.45, 广东: 0.55 } : { 湖北: 0.7, 广东: 0.3 },
      degrees: i % 3 ? { 本科: 0.7, 硕士: 0.3 } : { 大专: 0.2, 本科: 0.65, 硕士: 0.15 },
      experience: { '1-3年': 0.35, '3-5年': 0.5, '5年以上': 0.15 },
      salaryBands: { '10-20k': 0.2, '20-30k': 0.5, '30-40k': 0.3 }, techStacks: {}, medianSalary: 25, postCount: posts,
    },
    _vec: vec,
  }));
  const emerging = { ...jobs[0], id: 'J:new', name: '萌芽岗位', emerging: true, attrs: undefined };
  const nodes = [...skillNodes, ...jobs, emerging];
  const nodeById = new Map(nodes.map((n: any) => [n.id, n]));
  const edges: any[] = [
    { kind: 'S-SP', source: 'S:T-SW-01', target: 'SP:Python', effectiveWeight: 0.9 },
    { kind: 'S-SP', source: 'S:T-DATA-01', target: 'SP:SQL', effectiveWeight: 0.9 },
    { kind: 'S-SP', source: 'S:G-COMM-01', target: 'SP:Team', effectiveWeight: 0.9 },
  ];
  const weights: Record<string, Map<string, any>> = {};
  for (const j of jobs as any[]) {
    const v = j._vec as number[];
    weights[j.id] = new Map([
      ['S:T-SW-01', { total: v[0] }],
      ['S:T-DATA-01', { total: v[1] }],
      ['S:G-COMM-01', { total: v[2] }],
    ]);
  }
  return { nodes, nodeById, edges, jobs, weights };
});

vi.mock('@/data/generator', () => ({
  NOW: '2026-04',
  getDataset: () => ({ nodes: fx.nodes, edges: fx.edges, nodeById: fx.nodeById, signalMap: new Map() }),
  jobSkillWeights: (jobId: string) => fx.weights[jobId] ?? new Map(),
}));
vi.mock('@/data/realGraph', () => ({
  DEGREE_AXIS: ['大专', '本科', '硕士', '博士', '学历不限'],
  PROVINCE_AXIS: ['湖北', '广东', '其他'],
  cityCountsOf: (name: string) => name.includes('数据') ? { 武汉: 20, 深圳: 30 } : { 武汉: 50, 广州: 20 },
  profShareOf: () => [0.15, 0.5, 0.25, 0.1],
  provinceShare: (_name: string, allow: Set<string>) => allow.has('武汉') ? { 湖北: 1, 广东: 0 } : { 湖北: 0, 广东: 1 },
  skillCoverageOf: (_job: string, code: string) => code.includes('SW') ? 0.8 : 0.5,
}));

import {
  PROF_UNKNOWN, buildAttrGroups, buildClusters, buildOverview, buildPostCells, cityCountsIn,
  countPicks, dimRank, emptyPicks, exploreBase, filterByPicks, nearestJobs, profBandFrom, sortBySimilarity,
} from '@/data/explore';

describe('explore 职业探索取数层', () => {
  const ids = ['J:1', 'J:2', 'J:3', 'J:4', 'J:5', 'J:6'];

  it('实测熟练度分布按四档累计概率确定性落档', () => {
    expect(profBandFrom([1, 0, 0, 0] as any, 0.8, 'a', 'hard')).toBe(0);
    expect(profBandFrom([0, 1, 0, 0] as any, 0.8, 'b', 'hard')).toBe(1);
    expect(profBandFrom([0, 0, 1, 0] as any, 0.8, 'c', 'hard')).toBe(2);
    expect(profBandFrom([0, 0, 0, 1] as any, 0.8, 'd', 'hard')).toBe(PROF_UNKNOWN);
    expect([0, 1, 2, 3]).toContain(profBandFrom(null, 0.7, 'fallback', 'soft'));
  });

  it('基础模型撤除叠层技能/新岗位，并形成归一化能力构成和技能点映射', () => {
    const base = exploreBase();
    expect(base.axes).toHaveLength(3);
    expect(base.overlaySkills).toEqual([{ id: 'S:PS-01', name: '未来叠层技能' }]);
    expect(base.jobs.size).toBe(6);
    expect(base.jobs.has('J:new')).toBe(false);
    for (const j of base.jobs.values()) {
      expect(j.vector.reduce((a, b) => a + b, 0)).toBeCloseTo(1);
      expect(j.groupVector.reduce((a, b) => a + b, 0)).toBeCloseTo(1);
      expect(j.items.size).toBeGreaterThan(0);
    }
  });

  it('总览同时产出技能点、能力组、岗位和招聘总量，并支持保留岗位顺序', () => {
    const o = buildOverview(['J:4', 'J:1', 'J:3'], true);
    expect(o.itemRows.map((x) => x.name)).toEqual(expect.arrayContaining(['Python', 'SQL', '团队协作']));
    expect(o.groupRows).toHaveLength(3);
    expect(o.jobRows.map((j) => j.id)).toEqual(['J:4', 'J:1', 'J:3']);
    expect(o.totalPosts).toBe(280);
    expect(o.groupRows.some((r) => r.jobs.size > 0)).toBe(true);
  });

  it('三维属性分组、城市统计和组合筛选使用同一批岗位', () => {
    const groups = buildAttrGroups(ids, new Set(['武汉']));
    expect(groups.map((g) => g.kind)).toEqual(['cities', 'degrees', 'experience']);
    expect(groups[0].rows.find((r) => r.bucket === '湖北')!.posts).toBeGreaterThan(0);
    const cities = cityCountsIn(ids);
    expect(cities.武汉).toBeGreaterThan(0);
    expect(cities.深圳).toBeGreaterThan(0);
    const p = emptyPicks();
    expect(countPicks(p)).toBe(0);
    expect(filterByPicks(ids, p)).toEqual(ids);
    p.cities.add('湖北'); p.degrees.add('本科');
    expect(countPicks(p)).toBe(2);
    const filtered = filterByPicks(ids, p);
    expect(filtered.length).toBeGreaterThan(0);
    expect(filtered.length).toBeLessThanOrEqual(ids.length);
  });

  it('近邻传播聚类产出簇中心、二维投影、地平线分箱且结果缓存', () => {
    const model = buildClusters(ids);
    expect(model.clusters.length).toBeGreaterThan(0);
    expect(model.levelStep).toBeGreaterThan(0);
    expect(model.domainMax).toBeGreaterThan(0);
    expect(model.iterations).toBeGreaterThan(0);
    expect(buildClusters(ids)).toBe(model);
    for (const c of model.clusters) {
      expect(c.mean.length).toBe(3);
      expect(c.dist).toHaveLength(3);
      expect(c.xy.every((x) => Number.isFinite(x) && x >= 0 && x <= 1)).toBe(true);
    }
    expect(buildClusters([]).clusters).toEqual([]);
  });

  it('岗位分布图按学历×薪资拆格并限制格内岗位数', () => {
    const r = buildPostCells(ids, 2, 2, 0.1);
    expect(r.cells.length).toBeGreaterThan(0);
    expect(r.jobsShown).toBeGreaterThan(0);
    expect(r.hiddenJobs).toBeGreaterThanOrEqual(0);
    expect(r.coverage).toBeGreaterThan(0);
    expect(r.cells.every((c) => c.posts > 0 && c.vector.length === 3)).toBe(true);
  });

  it('相似度重排把锚点置顶，并返回固定数量的最近岗位', () => {
    const sorted = sortBySimilarity(ids, 'J:1');
    expect(sorted[0]).toBe('J:1');
    expect(nearestJobs(ids, 'J:1', 3)).toHaveLength(3);
    expect(sortBySimilarity(ids, 'missing')).toEqual(ids);
    expect(dimRank('hard')).toBeLessThan(dimRank('soft'));
  });
});
