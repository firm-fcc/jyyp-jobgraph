import { describe, expect, it, vi } from 'vitest';

const fx = vi.hoisted(() => {
  const edges = [
    { kind: 'S-SP', source: 'S:A', target: 'SP:Python', baseWeight: 0.8, deltaWeight: 0, effectiveWeight: 0.8 },
    { kind: 'S-SP', source: 'S:A', target: 'SP:PyTorch', baseWeight: 0.6, deltaWeight: 0, effectiveWeight: 0.6 },
    { kind: 'S-SP', source: 'S:B', target: 'SP:SQL', baseWeight: 0.7, deltaWeight: 0, effectiveWeight: 0.7 },
    { kind: 'J-T', source: 'J:1', target: 'T:1', baseWeight: 0.8, deltaWeight: 0.1 },
    { kind: 'T-S', source: 'T:1', target: 'S:A', baseWeight: 0.7, deltaWeight: 0 },
    { kind: 'T-S', source: 'T:1', target: 'S:B', baseWeight: 0.3, deltaWeight: 0 },
    { kind: 'J-S', source: 'J:1', target: 'S:A', baseWeight: 0.9, deltaWeight: 0.1 },
    { kind: 'J-S', source: 'J:1', target: 'S:B', baseWeight: 0.2, deltaWeight: 0 },
    { kind: 'J-S', source: 'J:2', target: 'S:A', baseWeight: 0.8, deltaWeight: 0 },
    { kind: 'J-S', source: 'J:2', target: 'S:B', baseWeight: 0.3, deltaWeight: 0 },
    { kind: 'J-S', source: 'J:3', target: 'S:A', baseWeight: 0.1, deltaWeight: 0 },
    { kind: 'J-S', source: 'J:3', target: 'S:B', baseWeight: 0.9, deltaWeight: 0 },
  ] as any[];
  const nodes = [
    { id: 'J:1', name: '算法工程师', kind: 'job', category: 'AI', attrs: { experience: { '3-5年': 10 }, degrees: { 本科: 8 } } },
    { id: 'J:2', name: '机器学习工程师', kind: 'job', category: 'AI', attrs: { experience: {}, degrees: {} } },
    { id: 'J:3', name: '数据工程师', kind: 'job', category: 'Data', attrs: { experience: {}, degrees: {} } },
    { id: 'T:1', name: '模型训练', kind: 'task' },
    { id: 'S:A', name: '机器学习', kind: 'skill' },
    { id: 'S:B', name: '数据处理', kind: 'skill' },
  ];
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const signalMap = new Map([['S:B', { leadMonths: { paper: 8 } }]]);
  const weights: Record<string, Map<string, any>> = {
    'J:1': new Map([
      ['S:A', { total: 1, direct: 0.8, viaTask: 0.2, viaTasks: new Map([['T:1', 0.2]]), overlay: 0.05, mix: { jd: 0.7, paper: 0.2, news: 0.1 }, confidence: 0.9 }],
      ['S:B', { total: 0.5, direct: 0.1, viaTask: 0.4, viaTasks: new Map([['T:1', 0.4]]), overlay: 0.2, mix: { jd: 0.4, paper: 0.4, news: 0.2 }, confidence: 0.8 }],
    ]),
    'J:2': new Map([
      ['S:A', { total: 0.9, direct: 0.9, viaTask: 0, viaTasks: new Map(), overlay: 0, mix: {}, confidence: 0.9 }],
      ['S:B', { total: 0.3, direct: 0.3, viaTask: 0, viaTasks: new Map(), overlay: 0, mix: {}, confidence: 0.8 }],
    ]),
    'J:3': new Map([
      ['S:A', { total: 0.2, direct: 0.2, viaTask: 0, viaTasks: new Map(), overlay: 0, mix: {}, confidence: 0.8 }],
      ['S:B', { total: 1, direct: 1, viaTask: 0, viaTasks: new Map(), overlay: 0, mix: {}, confidence: 0.9 }],
    ]),
  };
  return { edges, nodes, nodeById, signalMap, weights };
});
const { edges } = fx;

vi.mock('@/data/generator', () => ({
  NOW: '2026-04',
  getDataset: () => ({ edges: fx.edges, nodeById: fx.nodeById, signalMap: fx.signalMap, nodes: fx.nodes }),
  edgesFrom: (es: any[], kind: string, source: string) => es.filter((e) => e.kind === kind && e.source === source),
  jobSkillWeights: (jobId: string) => fx.weights[jobId] ?? new Map(),
}));
vi.mock('@/data/seeds', () => ({
  SEED_SKILL_POINTS: [
    { name: 'Python', level: 1, category: 'AI' },
    { name: 'PyTorch', level: 2, category: 'AI' },
    { name: 'SQL', level: 2, category: 'Data' },
  ],
}));

import {
  DIM_WEIGHTS,
  computeMatch,
  fitAgainst,
  jobDirectVector,
  jobVector,
  overall,
  projectJobs,
  projectResume,
  resumeToSkillVector,
  scoreAgainst,
  similarJobs,
} from '@/data/matching';

const resume = {
  skillPoints: [
    { id: 'SP:Python', name: 'Python', proficiency: 0.9 },
    { id: 'SP:PyTorch', name: 'PyTorch', proficiency: 0.6 },
    { id: 'S:B', name: '数据处理', proficiency: 0.3 },
  ],
  years: 2,
  degree: '本科',
} as any;

describe('matching 前端人岗匹配镜像', () => {
  it('简历技能点沿 S-SP 反向聚合，直接技能节点直接计入', () => {
    const v = resumeToSkillVector(resume, edges as any);
    expect(v['S:A']).toBeCloseTo((0.9 * 0.8 + 0.6 * 0.6) / 1.4);
    expect(v['S:B']).toBeCloseTo(0.3);
  });

  it('岗位向量按最大权重归一化，直达向量只看 J-S', () => {
    expect(jobVector('J:1')).toEqual({ 'S:A': 1, 'S:B': 0.5 });
    expect(jobDirectVector('J:1')).toEqual({ 'S:A': 1, 'S:B': 0.2 });
  });

  it('综合匹配度严格等于五维加权和', () => {
    expect(DIM_WEIGHTS.reduce((s, x) => s + x.weight, 0)).toBeCloseTo(1);
    const dims = { skill: 0.8, task: 0.6, domain: 0.7, experience: 0.5, degree: 1 } as any;
    const expected = 0.34 * 0.8 + 0.26 * 0.6 + 0.16 * 0.7 + 0.14 * 0.5 + 0.1;
    expect(overall(dims)).toBeCloseTo(expected);
  });

  it('完整岗位打分给出能力明细、任务覆盖和五维分数', () => {
    const rvec = resumeToSkillVector(resume, edges as any);
    const fit = fitAgainst(resume, rvec, 'J:1');
    expect(fit.score).toBeGreaterThan(0);
    expect(fit.score).toBeLessThanOrEqual(1);
    expect(fit.items.map((x) => x.name)).toEqual(expect.arrayContaining(['机器学习', '数据处理']));
    expect(fit.tasks).toHaveLength(1);
    expect(fit.tasks[0].taskName).toBe('模型训练');
    expect(fit.dims.degree).toBe(1);
    expect(scoreAgainst(resume, rvec, 'J:1')).toBe(fit.score);
  });

  it('computeMatch 复用同一打分并生成学习路径与建议', () => {
    const result = computeMatch(resume, 'J:1');
    expect(result.jobName).toBe('算法工程师');
    expect(result.path).toHaveLength(3);
    expect(result.path.map((p: any) => p.title)).toEqual(['筑基', '进阶', '前沿']);
    expect(result.advice.length).toBeGreaterThan(0);
  });

  it('相近岗位优先直达能力结构更接近者', () => {
    const got = similarJobs('J:1', ['J:1', 'J:2', 'J:3'], 2);
    expect(got[0].jobId).toBe('J:2');
    expect(got.every((x) => x.jobId !== 'J:1')).toBe(true);
  });

  it('岗位投影与简历加权重心输出有限坐标', () => {
    const ids = ['J:1', 'J:2', 'J:3'];
    const coords = projectJobs(ids);
    expect(coords.size).toBe(3);
    for (const c of coords.values()) expect(c.every(Number.isFinite)).toBe(true);
    const p = projectResume(ids, coords, (j) => j === 'J:1' ? 0.9 : 0.3);
    expect(p.every(Number.isFinite)).toBe(true);
    expect(projectResume(ids, coords, () => 0)).toEqual([0, 0]);
  });
});
