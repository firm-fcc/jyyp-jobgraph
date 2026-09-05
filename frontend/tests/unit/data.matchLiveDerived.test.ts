import { beforeEach, describe, expect, it, vi } from 'vitest';

const fx = vi.hoisted(() => {
  const edges = [
    { kind: 'J-T', source: 'J:1', target: 'T:1', baseWeight: 0.8, deltaWeight: 0.1 },
    { kind: 'T-S', source: 'T:1', target: 'S:A', baseWeight: 0.7, deltaWeight: 0.0 },
    { kind: 'T-S', source: 'T:1', target: 'S:B', baseWeight: 0.3, deltaWeight: 0.0 },
  ];
  const nodeById = new Map([
    ['T:1', { id: 'T:1', name: '训练模型' }],
    ['S:A', { id: 'S:A', name: '机器学习' }],
    ['S:B', { id: 'S:B', name: '数据处理' }],
  ]);
  return { edges, nodeById };
});
const { edges, nodeById } = fx;

vi.mock('@/data/generator', () => ({
  getDataset: () => ({ edges: fx.edges, nodeById: fx.nodeById }),
  edgesFrom: (es: any[], kind: string, source: string) => es.filter((e) => e.kind === kind && e.source === source),
}));
vi.mock('@/data/realGraph', () => ({
  jobRawSource: () => ({ samples: [{ text: '负责机器学习模型训练与评估。' }], attrib: {} }),
}));
vi.mock('@/data/matching', () => ({
  jobDirectVector: (jobId: string) => jobId === 'J:empty' ? {} : jobId === 'J:1' ? { 'S:A': 1, 'S:B': 0.2 } : { 'S:A': 0.2, 'S:B': 1 },
  jobVector: () => ({ 'S:A': 0.6, 'S:B': 0.6 }),
}));

import {
  graphReqVecOf,
  liveJobCoverage,
  liveSimilarJobs,
  liveSkillVector,
  liveTaskCoverage,
  makeReqVecOf,
  reqSimilarity,
  segmentResume,
  summaryReqVec,
} from '@/data/matchLiveDerived';

describe('matchLiveDerived 实测链路派生逻辑', () => {
  beforeEach(() => vi.clearAllMocks());

  it('候选人能力向量优先使用熟练度档位，缺失档位退到判定状态', () => {
    const profile = {
      assessments: [
        { team_skill_id: 'A', status: 'supported' },
        { team_skill_id: 'B', status: 'partially_supported' },
        { team_skill_id: 'C', status: 'unsupported' },
      ],
    } as any;
    const skillNodes = new Map([
      ['A', { id: 'S:A' }],
      ['B', { id: 'S:B' }],
    ] as any);
    const v = liveSkillVector(profile, { A: 'P4' }, skillNodes);
    expect(v['S:A']).toBe(1);
    expect(v['S:B']).toBe(0.4);
    expect(v['S:C']).toBe(0);
  });

  it('岗位汇总向量按最大提及率归一化并跳过零值', () => {
    const v = summaryReqVec({ skills: [
      { team_skill_id: 'A', jd_presence_rate: 0.8 },
      { team_skill_id: 'B', jd_presence_rate: 0.4 },
      { team_skill_id: 'C', jd_presence_rate: 0 },
    ] } as any);
    expect(v).toEqual({ 'S:A': 1, 'S:B': 0.5 });
  });

  it('要求向量优先后端汇总，无汇总时退回图谱直达边，再退回两跳', () => {
    const sums = new Map<string, any>([['1', { skills: [{ team_skill_id: 'A', jd_presence_rate: 0.5 }] }], ['empty', null]]);
    const req = makeReqVecOf(sums);
    expect(req('J:1')).toEqual({ 'S:A': 1 });
    expect(req('J:2')).toEqual({ 'S:A': 0.2, 'S:B': 1 });
    expect(graphReqVecOf('J:empty')).toEqual({ 'S:A': 0.6, 'S:B': 0.6 });
  });

  it('相似度处理正交、同向和零向量', () => {
    expect(reqSimilarity({ a: 1 }, { a: 2 })).toBeCloseTo(1);
    expect(reqSimilarity({ a: 1 }, { b: 1 })).toBe(0);
    expect(reqSimilarity({}, { a: 1 })).toBe(0);
  });

  it('相近岗位按相似度排序并排除自身', () => {
    const req = (j: string) => ({ A: j === 'J:1' || j === 'J:2' ? 1 : 0, B: j === 'J:3' ? 1 : 0 });
    const got = liveSimilarJobs('J:1', ['J:1', 'J:2', 'J:3'], req, 2);
    expect(got[0]).toEqual({ jobId: 'J:2', sim: 1 });
    expect(got.every((x) => x.jobId !== 'J:1')).toBe(true);
  });

  it('岗位能力覆盖按要求权重与达成率加权', () => {
    const req = () => ({ 'S:A': 1, 'S:B': 0.5, 'S:C': 0.01 });
    const score = liveJobCoverage('J:1', { 'S:A': 1, 'S:B': 0.25 }, req);
    expect(score).toBeCloseTo((1 + 0.5 * 0.5) / 1.5);
  });

  it('任务覆盖沿岗位-任务-能力关系计算，并报告最弱能力', () => {
    const req = () => ({ 'S:A': 1, 'S:B': 1 });
    const got = liveTaskCoverage('J:1', { 'S:A': 1, 'S:B': 0 }, req);
    expect(got).toHaveLength(1);
    expect(got[0].taskName).toBe('训练模型');
    expect(got[0].coverage).toBeCloseTo(0.7);
    expect(got[0].weakest).toContain('数据处理');
  });

  it('按小节标题与日期范围把简历切成工作/项目/教育经历', () => {
    const text = [
      '工作经历',
      '某科技公司',
      '算法工程师',
      '2023-01 - 2024-06',
      '负责模型训练',
      '项目经历',
      '智能推荐系统',
      '2024-07 - 2024-12',
      '核心开发',
      '教育经历',
      '华中科技大学',
      '2021-09 - 2025-06',
    ].join('\n');
    const segs = segmentResume(text);
    expect(segs.length).toBeGreaterThanOrEqual(3);
    expect(segs.some((s) => s.kind === 'work')).toBe(true);
    expect(segs.some((s) => s.kind === 'project')).toBe(true);
    expect(segs.some((s) => s.kind === 'education')).toBe(true);
    expect(segmentResume('   ')).toEqual([]);
  });
});
