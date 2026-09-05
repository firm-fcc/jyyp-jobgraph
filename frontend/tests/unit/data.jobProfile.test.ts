import { describe, expect, it, vi } from 'vitest';

const fx = vi.hoisted(() => {
  const nodes: any[] = [
    { id: 'J:ai1', kind: 'job', name: '算法工程师', topCategory: '人工智能', category: 'AI', realCount: 100 },
    { id: 'J:ai2', kind: 'job', name: '机器学习工程师', topCategory: '人工智能', category: 'AI', realCount: 80 },
    { id: 'J:data', kind: 'job', name: '数据工程师', topCategory: '大数据', category: 'Data', realCount: 60 },
    { id: 'J:orphan', kind: 'job', name: '萌芽岗位', topCategory: '无一级归属', category: 'AI', realCount: 1 },
    { id: 'T:model', kind: 'task', name: '模型训练', category: '' },
    { id: 'T:data', kind: 'task', name: '数据处理', category: '' },
    { id: 'S:alg', kind: 'skill', name: '软件与算法', category: '技术能力', confidence: 0.9 },
    { id: 'S:comm', kind: 'skill', name: '沟通协作', category: '基础通用', confidence: 0.8 },
    { id: 'SP:py', kind: 'skillpoint', name: 'Python', category: '技术能力', confidence: 0.9 },
    { id: 'SP:team', kind: 'skillpoint', name: '团队协作', category: '基础通用', confidence: 0.8 },
  ];
  const edges: any[] = [
    { kind: 'J-T', source: 'J:ai1', target: 'T:model', baseWeight: 0.8, deltaWeight: 0.1, confidence: 0.9 },
    { kind: 'J-T', source: 'J:ai2', target: 'T:model', baseWeight: 0.7, deltaWeight: 0.0, confidence: 0.8 },
    { kind: 'J-T', source: 'J:data', target: 'T:data', baseWeight: 0.9, deltaWeight: 0.0, confidence: 0.9 },
    { kind: 'T-S', source: 'T:model', target: 'S:alg', baseWeight: 0.8, deltaWeight: 0, confidence: 0.9 },
    { kind: 'T-S', source: 'T:model', target: 'S:comm', baseWeight: 0.2, deltaWeight: 0, confidence: 0.8 },
    { kind: 'T-S', source: 'T:data', target: 'S:alg', baseWeight: 0.6, deltaWeight: 0, confidence: 0.8 },
    { kind: 'T-S', source: 'T:data', target: 'S:comm', baseWeight: 0.4, deltaWeight: 0, confidence: 0.8 },
    { kind: 'S-SP', source: 'S:alg', target: 'SP:py', baseWeight: 0.9, deltaWeight: 0.1, confidence: 0.9 },
    { kind: 'S-SP', source: 'S:comm', target: 'SP:team', baseWeight: 0.8, deltaWeight: 0, confidence: 0.8 },
  ];
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const weights: Record<string, Map<string, any>> = {
    'J:ai1': new Map([
      ['S:alg', { total: 1, overlay: 0.15, confidence: 0.9 }],
      ['S:comm', { total: 0.3, overlay: 0.02, confidence: 0.8 }],
    ]),
    'J:ai2': new Map([
      ['S:alg', { total: 0.8, overlay: 0.05, confidence: 0.85 }],
      ['S:comm', { total: 0.4, overlay: 0.01, confidence: 0.8 }],
    ]),
    'J:data': new Map([
      ['S:alg', { total: 0.6, overlay: 0, confidence: 0.8 }],
      ['S:comm', { total: 0.5, overlay: 0, confidence: 0.8 }],
    ]),
  };
  return { nodes, edges, nodeById, weights };
});

vi.mock('@/data/generator', () => ({
  NOW: '2026-04',
  getDataset: () => ({ nodes: fx.nodes, edges: fx.edges, nodeById: fx.nodeById, signalMap: new Map() }),
  edgesFrom: (edges: any[], kind: string, source: string) => edges.filter((e) => e.kind === kind && e.source === source),
  jobSkillWeights: (jobId: string) => fx.weights[jobId] ?? new Map(),
}));
vi.mock('@/data/realGraph', () => ({ LEVEL_TILT_MEASURED: null }));
vi.mock('@/data/realTaxonomy', () => ({ ORPHAN_CLUSTER: '无一级归属' }));

import { JOB_LEVELS, LEVEL_TILT_OF, jobCategories, jobProfile, profileScale } from '@/data/jobProfile';

describe('jobProfile 岗位能力剖面', () => {
  it('给出三档职级，并按岗位数量输出大类且隐藏无一级归属', () => {
    expect(JOB_LEVELS.map((x) => x.v)).toEqual(['junior', 'mid', 'senior']);
    expect(jobCategories()).toEqual([
      { name: '人工智能', count: 2 },
      { name: '大数据', count: 1 },
    ]);
  });

  it('单岗位剖面同时生成任务、能力组和技能点要求', () => {
    const scope = { kind: 'job', id: 'J:ai1', label: '算法工程师', jobCount: 1 } as const;
    const p = jobProfile(scope, 'mid');
    expect(p.scope).toBe(scope);
    expect(p.tasks[0].name).toBe('模型训练');
    expect(p.skills.map((x) => x.name)).toEqual(expect.arrayContaining(['软件与算法', '沟通协作']));
    expect(p.points.map((x) => x.name)).toEqual(expect.arrayContaining(['Python', '团队协作']));
    expect(p.skills.every((x) => x.demand > 0 && x.confidence > 0)).toBe(true);
    expect(p.points.every((x) => x.forwardShare >= 0 && x.forwardShare <= 1)).toBe(true);
  });

  it('职级倾斜使执行型能力在初级更高、沟通能力在高级更高', () => {
    expect(LEVEL_TILT_OF('软件与算法', 'junior')).toBeGreaterThan(LEVEL_TILT_OF('软件与算法', 'senior'));
    expect(LEVEL_TILT_OF('沟通协作', 'senior')).toBeGreaterThan(LEVEL_TILT_OF('沟通协作', 'junior'));
    const scope = { kind: 'job', id: 'J:ai1', label: '算法工程师', jobCount: 1 } as const;
    const junior = jobProfile(scope, 'junior');
    const senior = jobProfile(scope, 'senior');
    expect(junior.skills.find((x) => x.id === 'S:alg')!.demand).toBeGreaterThan(senior.skills.find((x) => x.id === 'S:alg')!.demand);
    expect(senior.skills.find((x) => x.id === 'S:comm')!.demand).toBeGreaterThan(junior.skills.find((x) => x.id === 'S:comm')!.demand);
  });

  it('大类/领域范围支持平均聚合，标度固定为正数并缓存复用', () => {
    const cat = jobProfile({ kind: 'category', id: '人工智能', label: '人工智能', jobCount: 2 }, 'senior');
    const all = jobProfile({ kind: 'all', id: null, label: '领域整体', jobCount: 3 }, 'senior');
    expect(cat.skills.length).toBeGreaterThan(0);
    expect(all.tasks.length).toBeGreaterThan(0);
    const s1 = profileScale('all');
    const s2 = profileScale('focus');
    expect(s1.task).toBeGreaterThan(0);
    expect(s1.skill).toBeGreaterThan(0);
    expect(s1.point).toBeGreaterThan(0);
    expect(s2.task).toBeGreaterThan(0);
    expect(profileScale('all')).toBe(s1);
  });
});
