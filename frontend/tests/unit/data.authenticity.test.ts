import { describe, expect, it, vi } from 'vitest';

const fx = vi.hoisted(() => {
  const edges = [
    { kind: 'S-SP', source: 'S:A', target: 'SP:Python', effectiveWeight: 0.8 },
    { kind: 'J-T', source: 'J:1', target: 'T:1', effectiveWeight: 0.9, evidence: [] },
    { kind: 'T-S', source: 'T:1', target: 'S:A', effectiveWeight: 0.8 },
    { source: 'J:1', kind: 'J-S', target: 'S:A', evidence: [{ sourceType: 'jd', snippet: '负责机器学习模型训练与评估' }] },
  ] as any[];
  const nodeById = new Map([
    ['S:A', { id: 'S:A', name: '机器学习' }],
    ['T:1', { id: 'T:1', name: '模型训练' }],
    ['SP:Python', { id: 'SP:Python', name: 'Python' }],
  ]);
  return { edges, nodeById };
});

vi.mock('@/data/taxonomy', () => ({ NOISE_PHRASES: ['责任心强'], }));
vi.mock('@/data/seeds', () => ({ SEED_SKILL_POINTS: [{ name: 'Python', category: 'AI' }, { name: 'SQL', category: 'Data' }] }));
vi.mock('@/data/demoFill', () => ({
  resolveClaim: (raw: string) => raw === 'Python'
    ? { id: 'SP:Python', mappedName: 'Python' }
    : raw === '机器学习'
      ? { id: 'S:A', mappedName: '机器学习' }
      : { id: `SP:${raw}`, mappedName: raw },
}));
vi.mock('@/data/generator', () => ({ getDataset: () => ({ edges: fx.edges, nodeById: fx.nodeById }) }));
vi.mock('@/data/matching', () => ({ jobVector: () => ({ 'S:A': 1 }) }));

import { auditResume, docLinkOf, linkExperiences } from '@/data/authenticity';

const resume = {
  years: 1,
  degree: '本科',
  sections: [{
    title: '项目经历',
    lines: [
      { id: 'l1', text: '负责机器学习模型训练与评估，准确率提升 20%' },
      { id: 'l2', text: '论文 2 篇' },
      { id: 'l3', text: '论文 3 篇' },
    ],
  }],
  skillPoints: [
    { id: 'SP:Python', name: 'Python', proficiency: 0.9, from: 'list', anchors: ['l1'] },
    { id: 'SP:SQL', name: 'SQL', proficiency: 0.8, from: 'list', anchors: ['l1'] },
  ],
  experiences: [
    { id: 'e1', kind: 'project', title: '推荐系统', months: 12, claims: ['Python', '未知框架'], lines: ['l1'] },
  ],
} as any;

describe('authenticity 简历真实性与经历关联', () => {
  it('真实性核验固定输出七项检查并识别可疑表述', () => {
    const report = auditResume(resume, 'J:1');
    expect(report.checks).toHaveLength(7);
    expect(report.score).toBeLessThan(100);
    expect(report.checks.find((c) => c.id === 'claim-vs-experience')?.items).toContain('SQL');
    expect(report.checks.find((c) => c.id === 'metric-basis')?.level).not.toBe('pass');
    expect(report.checks.find((c) => c.id === 'count-conflict')?.level).toBe('watch');
  });

  it('经历关联把技能声明映射到岗位能力、任务与未对齐项', () => {
    const links = linkExperiences(resume, 'J:1');
    expect(links).toHaveLength(1);
    expect(links[0].coverage).toBeGreaterThan(0);
    expect(links[0].hits[0].name).toBe('机器学习');
    expect(links[0].tasks).toContain('模型训练');
    expect(links[0].unmapped).toContain('未知框架');
  });

  it('官方资料链接只返回白名单技能', () => {
    expect(docLinkOf('Python')).toContain('python.org');
    expect(docLinkOf('不存在技能')).toBeUndefined();
  });
});

