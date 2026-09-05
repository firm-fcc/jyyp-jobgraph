import { describe, expect, it } from 'vitest';
import {
  buildEvidenceIndex,
  buildLiveAdvice,
  buildLiveItems,
  buildLivePath,
  excludedRequirements,
  levelLabel,
  requiredLabel,
  summarizeCandidate,
} from '@/data/matchLive';

const skillNodes = new Map([
  ['S1', { id: 'S1', name: '机器学习', kind: 'skill', category: '人工智能', definition: '模型训练与评估' }],
  ['S2', { id: 'S2', name: '数据工程', kind: 'skill', category: '数据', definition: '数据处理' }],
] as never);

const target = {
  skills: [
    {
      team_skill_id: 'S1', team_skill_name: 'ML', skill_type: 'hard', requirement_status: 'GRADED',
      market_signal: { effective_weight: 0.8, delta_weight: 0.1 },
      requirement_statistics: {
        jd_count: 100, jd_presence_count: 80, jd_presence_rate: 0.8,
        graded_posting_count: 40, graded_ratio: 0.5, level_distribution: { P2: 20, P3: 20 },
      },
    },
    {
      team_skill_id: 'S2', team_skill_name: 'Data', skill_type: 'hard', requirement_status: 'AUXILIARY_NOT_GRADED',
      market_signal: { effective_weight: 0.4, delta_weight: 0 },
    },
  ],
} as any;

const match = {
  skills: [
    {
      team_skill_id: 'S1', team_skill_name: 'ML', required_level: 'P3', candidate_level: 'P2',
      gap_type: 'LEVEL_GAP', path_mode: 'DEEPEN',
      candidate_evidence: [{ text: '完成模型训练', source_id: 'exp-1', start: 3, end: 9 }],
      requirement_evidence: ['jd-1'], explanation: '等级不足',
    },
    {
      team_skill_id: 'S2', team_skill_name: 'Data', required_level: null, candidate_level: null,
      gap_type: 'MISSING', path_mode: 'LEARN', candidate_evidence: [], requirement_evidence: [], explanation: '缺失',
    },
  ],
  summary: { satisfied: 0, missing: 1, level_gap: 1, evidence_insufficient: 0, required_skills: 2 },
} as any;

describe('matchLive 后端匹配结果适配', () => {
  it('熟练度标签覆盖有值与空值', () => {
    expect(levelLabel('P3')).toBe('熟练');
    expect(levelLabel(null)).toBe('未标明等级');
    expect(requiredLabel('U')).toBe('无法定级');
  });

  it('把后端逐项结果挂回图谱并按差距严重度排序', () => {
    const items = buildLiveItems(match, target, skillNodes as any);
    expect(items.map((i) => i.gap)).toEqual(['MISSING', 'LEVEL_GAP']);
    const ml = items.find((i) => i.teamSkillId === 'S1')!;
    expect(ml.name).toBe('机器学习');
    expect(ml.dimension).toBe('人工智能');
    expect(ml.demand).toBe(0.8);
    expect(ml.demandGraded).toBe(0.4);
    expect(ml.attain).toBeCloseTo(2 / 3);
    expect(ml.forwardLooking).toBe(true);
  });

  it('无逐条统计时使用市场信号作为 demand', () => {
    const items = buildLiveItems(match, target, skillNodes as any);
    const data = items.find((i) => i.teamSkillId === 'S2')!;
    expect(data.demand).toBe(0.4);
    expect(data.demandGraded).toBe(0.4);
    expect(data.attain).toBe(0);
  });

  it('列出不参与评级的岗位要求及理由', () => {
    const excluded = excludedRequirements(target, skillNodes as any);
    expect(excluded).toEqual([{ teamSkillId: 'S2', name: '数据工程', reason: '辅助能力，本版不参与评级计分' }]);
  });

  it('从差距结果归纳可解释建议', () => {
    const items = buildLiveItems(match, target, skillNodes as any);
    const advice = buildLiveAdvice(match.summary, items);
    expect(advice[0].title).toContain('已满足 0 项');
    expect(advice.some((a) => a.title.includes('优先补齐'))).toBe(true);
    expect(advice.some((a) => a.title.includes('等级不足'))).toBe(true);
  });

  it('证据倒排可去重并合并引用技能', () => {
    const items = buildLiveItems(match, target, skillNodes as any);
    items.push({ ...items[1], name: '另一能力', evidence: [{ text: '完成模型训练', sourceId: 'exp-1', start: 3, end: 9 }] });
    const ev = buildEvidenceIndex(items);
    expect(ev).toHaveLength(1);
    expect(ev[0].skills).toEqual(expect.arrayContaining(['机器学习', '另一能力']));
  });

  it('学习路径按 READY / GRAPH_UNAVAILABLE / noAction 分档', () => {
    const res = {
      path_status: 'READY',
      diagnostics: { curated_graph_count: 2 },
      rendered: { skill_paths: [
        {
          team_skill_id: 'S1', team_skill_name: 'S1', gap_type: 'LEVEL_GAP', path_mode: 'DEEPEN', required_level: 'P3', observed_level: 'P2',
          current_state: '应用', development_goal: '熟练', path_status: 'READY', reassessment_guidance: '复测',
          learning_steps: [{ node_id: 'n1', node_name: '进阶训练', evidence_task: '完成项目', validation_criteria: ['可复现'] }],
          capstone_guidance: null, verification_guidance: null,
        },
        {
          team_skill_id: 'S2', team_skill_name: 'S2', gap_type: 'MISSING', path_mode: 'LEARN', required_level: 'P2', observed_level: null,
          current_state: '缺失', development_goal: '应用', path_status: 'GRAPH_UNAVAILABLE', reassessment_guidance: '补齐后复测', learning_steps: [],
          capstone_guidance: null, verification_guidance: null,
        },
        {
          team_skill_id: 'S3', team_skill_name: 'S3', gap_type: 'SATISFIED', path_mode: 'NONE', required_level: 'P2', observed_level: 'P2',
          current_state: '满足', development_goal: '保持', path_status: 'NO_ACTION', reassessment_guidance: '', learning_steps: [],
          capstone_guidance: null, verification_guidance: null,
        },
      ] },
    } as any;
    const path = buildLivePath(res, skillNodes as any);
    expect(path.ready).toHaveLength(1);
    expect(path.ready[0].name).toBe('机器学习');
    expect(path.unavailable).toHaveLength(1);
    expect(path.noAction).toHaveLength(1);
    expect(path.curatedGraphCount).toBe(2);
  });

  it('候选人摘要统计支持、部分支持、未支持、证据数和平均置信度', () => {
    const s = summarizeCandidate({
      candidate_id: 'c1', skill_registry_version: 'v1', assessments: [
        { status: 'supported', evidence: [1, 2], confidence: 0.9 },
        { status: 'partially_supported', evidence: [1], confidence: 0.7 },
        { status: 'unsupported', evidence: [], confidence: null },
      ],
    });
    expect(s).toMatchObject({ supported: 1, partial: 1, unsupported: 1, evidenceCount: 3 });
    expect(s.avgConfidence).toBeCloseTo(0.8);
  });
});
