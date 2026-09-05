import { describe, expect, it, vi } from 'vitest';

const fx = vi.hoisted(() => {
  const axes = [
    { id: 'S:alg', name: '算法', dim: '技术能力', group: '软件与算法', type: 'hard' },
    { id: 'S:data', name: '数据', dim: '技术能力', group: '数据与计算科学', type: 'hard' },
    { id: 'S:comm', name: '沟通', dim: '基础通用能力', group: '沟通协作', type: 'soft' },
  ];
  const attrs = {
    cities: { 湖北: 0.6, 广东: 0.4 }, degrees: { 本科: 0.7, 硕士: 0.3 }, experience: { '3-5年': 0.8, '5年以上': 0.2 },
    salaryBands: { '20-30k': 1 }, techStacks: {}, medianSalary: 25, postCount: 100,
  };
  const job = { id: 'J:1', name: '算法工程师', cluster: '人工智能', posts: 100, vector: [0.6, 0.3, 0.1], groupVector: [0.6, 0.3, 0.1], items: new Map(), mix: { hard: 0.9, soft: 0.1 }, attrs, emerging: false };
  const node = { id: 'J:1', name: '算法工程师', funtypes: ['算法/机器学习'], keywords: ['Python', '模型'], definition: '负责算法研发', boundary: '区别于纯数据岗位', coreDuties: ['训练模型'], mustSkills: ['Python'], plusSkills: ['PyTorch'], scenarios: ['推荐系统'] };
  return { axes, job, node };
});

vi.mock('@/data/provinces', () => ({
  PROVINCES_ALL: ['湖北', '广东'],
  PROVINCE_OTHER: '其他',
  citiesOf: (p: string) => p === '湖北' ? ['武汉', '宜昌'] : p === '广东' ? ['广州', '深圳'] : ['其他'],
}));
vi.mock('@/data/explore', () => ({
  dimRank: (t: string | undefined) => t === 'hard' ? 0 : 1,
  exploreBase: () => ({ axes: fx.axes, jobs: new Map([['J:1', fx.job]]), nodeById: new Map([['J:1', fx.node]]) }),
}));

import { PROVINCES, clusterRadius, layoutClusters, postDetail, postMapLayout, skillTree } from '@/data/jobviz';

describe('jobviz 职业探索可视化数据层', () => {
  it('构建维度→能力组→技能三级树，并按硬技能维度优先', () => {
    const tree = skillTree();
    expect(tree).toHaveLength(2);
    expect(tree[0].name).toBe('技术能力');
    expect(tree[0].groups.map((g) => g.name)).toEqual(['软件与算法', '数据与计算科学']);
    expect(tree[1].groups[0].items).toEqual(['S:comm']);
    expect(skillTree()).toBe(tree);
  });

  it('省市两级词表完整生成', () => {
    expect(PROVINCES).toEqual([
      { name: '湖北', cities: ['武汉', '宜昌'] },
      { name: '广东', cities: ['广州', '深圳'] },
      { name: '其他', cities: ['其他'] },
    ]);
  });

  it('簇半径有上下界，碰撞布局确定且落在画布内', () => {
    expect(clusterRadius(0, 400, 300)).toBe(24);
    expect(clusterRadius(1000, 200, 100)).toBeGreaterThanOrEqual(18);
    expect(clusterRadius(1, 2000, 2000)).toBeLessThanOrEqual(40);
    const clusters = [
      { id: 'c1', xy: [0.5, 0.5], jobIds: ['J:1'], exemplar: 'J:1' },
      { id: 'c2', xy: [0.5, 0.5], jobIds: ['J:2', 'J:3'], exemplar: 'J:2' },
      { id: 'c3', xy: [0.1, 0.1], jobIds: ['J:4'], exemplar: 'J:4' },
    ] as any;
    const a = layoutClusters(clusters, 400, 300, 24, 'same');
    const b = layoutClusters(clusters, 400, 300, 24, 'same');
    expect(a).toEqual(b);
    expect(a).toHaveLength(3);
    for (const [x, y] of a) {
      expect(x).toBeGreaterThan(0); expect(x).toBeLessThan(400);
      expect(y).toBeGreaterThan(0); expect(y).toBeLessThan(300);
    }
  });

  it('岗位分布图按学历×薪资格计算轴和确定性散点', () => {
    const cells = [
      { jobId: 'J:1', jobName: '算法工程师', cc: '本科', band: '20-30k', posts: 20, vector: [1, 0] },
      { jobId: 'J:2', jobName: '机器学习工程师', cc: '本科', band: '20-30k', posts: 10, vector: [0.8, 0.2] },
      { jobId: 'J:3', jobName: '数据工程师', cc: '硕士', band: '30-40k', posts: 5, vector: [0.2, 0.8] },
    ] as any;
    const m1 = postMapLayout(cells, ['本科', '硕士', '博士'], ['20-30k', '30-40k'], 600, 400, 'seed');
    const m2 = postMapLayout(cells, ['本科', '硕士', '博士'], ['20-30k', '30-40k'], 600, 400, 'seed');
    expect(m1.cols.map((x) => x.key)).toEqual(['本科', '硕士']);
    expect(m1.rows.map((x) => x.key)).toEqual(['20-30k', '30-40k']);
    expect(m1.glyphs).toHaveLength(3);
    expect(m1.glyphs.map((g) => [g.x, g.y])).toEqual(m2.glyphs.map((g) => [g.x, g.y]));
    expect(m1.glyphs.every((g) => g.r >= 2.5)).toBe(true);
  });

  it('详情表只展示实际可溯源字段并包含长文本定义', () => {
    const cell = { jobId: 'J:1', jobName: '算法工程师', cc: '本科', band: '20-30k', posts: 25, vector: [0.6, 0.3, 0.1] } as any;
    const d = postDetail(cell, '#123456');
    expect(d.title).toBe('算法工程师');
    expect(d.color).toBe('#123456');
    expect(d.rows.find((r) => r.label === '本格招聘信息条数')?.value).toContain('25');
    expect(d.rows.find((r) => r.label === '主要省份')?.value).toContain('湖北');
    expect(d.rows.find((r) => r.label === '岗位定义')?.value).toBe('负责算法研发');
    expect(d.rows.find((r) => r.label === '岗位定义')?.long).toBe(true);
  });
});
