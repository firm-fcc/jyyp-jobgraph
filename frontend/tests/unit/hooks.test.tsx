import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSize } from '@/hooks/useSize';
import { useZoomPan } from '@/hooks/useZoomPan';

const api = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  fetchJdList: vi.fn(),
  fetchJobIndex: vi.fn(),
  fetchJobSummary: vi.fn(),
  fetchTargetJob: vi.fn(),
  preflightResume: vi.fn(),
  extractCandidate: vi.fn(),
  runMatch: vi.fn(),
  runLearningPath: vi.fn(),
}));

vi.mock('@/api/matchApi', () => ({
  LIVE_MATCH: true,
  MATCH_WINDOW: '2022-10',
  MatchApiError: class MatchApiError extends Error { constructor(public status: number, public detail: string) { super(detail); } },
  ...api,
}));

import { matchProgress, useBackendHealth, useJdList, useJobIndex, useJobSummaries, useMatchUi, useLiveMatch } from '@/hooks/useMatchBackend';

describe('useSize', () => {
  it('读取实际尺寸并扣除边框', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 120.8, height: 80.9, x: 0, y: 0, top: 0, left: 0, right: 120.8, bottom: 80.9, toJSON() {},
    } as DOMRect);
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      borderLeftWidth: '1px', borderRightWidth: '2px', borderTopWidth: '1px', borderBottomWidth: '1px',
    } as CSSStyleDeclaration);
    function Probe() {
      const { ref, w, h } = useSize<HTMLDivElement>();
      return <><div ref={ref} /><output>{w}x{h}</output></>;
    }
    render(<Probe />);
    await waitFor(() => expect(screen.getByText('117x78')).toBeInTheDocument());
  });
});

describe('useZoomPan', () => {
  it('支持放大、缩小、适配区域和复位', () => {
    const { result } = renderHook(() => useZoomPan({ w: 400, h: 300, maxK: 4, step: 2 }));
    expect(result.current.k).toBe(1);
    act(() => result.current.zoomIn());
    expect(result.current.k).toBe(2);
    act(() => result.current.zoomOut());
    expect(result.current.k).toBe(1);
    act(() => result.current.fitTo({ x0: 100, y0: 80, x1: 200, y1: 160 }, 20, 3));
    expect(result.current.k).toBeGreaterThan(1);
    expect(result.current.tx).toBeLessThanOrEqual(0);
    act(() => result.current.showAll());
    expect(result.current).toMatchObject({ k: 1, tx: 0, ty: 0 });
  });

  it('倍率为 1 时忽略拖动，放大后可进入 panning', () => {
    const { result } = renderHook(() => useZoomPan({ w: 400, h: 300 }));
    act(() => result.current.onPointerDown({ button: 0, clientX: 10, clientY: 10 } as any));
    expect(result.current.panning).toBe(false);
    act(() => result.current.zoomIn());
    act(() => result.current.onPointerDown({ button: 0, clientX: 10, clientY: 10 } as any));
    expect(result.current.panning).toBe(true);
    act(() => window.dispatchEvent(new Event('pointerup')));
    expect(result.current.panning).toBe(false);
  });
});

describe('useMatchBackend 轻量请求 hooks', () => {
  beforeEach(() => {
    api.fetchHealth.mockResolvedValue({ status: 'ok', service: 'match' });
    api.fetchJdList.mockResolvedValue({ items: [{ jd_key: 'k1' }], total: 1 });
    api.fetchJobIndex.mockResolvedValue({ counts: { 算法工程师: 12 } });
    api.fetchJobSummary.mockImplementation((code: string) => code === 'bad' ? Promise.reject(new Error('404')) : Promise.resolve({ job_code: code }));
    api.preflightResume.mockResolvedValue({ quality: { fallback_required: false }, pages: 1 });
    api.extractCandidate.mockResolvedValue({ candidate_id: 'C1', candidate_skill_profile: { assessments: [{ team_skill_id: 'A', status: 'supported' }] }, resume_text: 'Python 项目经历', explicit_skill_mentions: ['Python'] });
    api.runMatch.mockResolvedValue({ score: 0.86, proficiency: { levels: { A: 'P3' } } });
    api.runLearningPath.mockResolvedValue({ stages: [{ title: '补齐能力' }] });
  });

  it('健康探测从 probing 转为 ready', async () => {
    const { result } = renderHook(() => useBackendHealth());
    expect(result.current.status).toBe('probing');
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.health).toMatchObject({ service: 'match' });
  });

  it('JD 列表记录 loading、条目数和 loadedFor', async () => {
    const { result, rerender } = renderHook(({ job, enabled }) => useJdList(job, enabled), {
      initialProps: { job: '算法工程师' as string | null, enabled: true },
    });
    await waitFor(() => expect(result.current.loadedFor).toBe('算法工程师'));
    expect(result.current.total).toBe(1);
    rerender({ job: null, enabled: false });
    await waitFor(() => expect(result.current.items).toEqual([]));
  });

  it('岗位索引获取失败时容错为 null', async () => {
    const { result } = renderHook(() => useJobIndex(true));
    await waitFor(() => expect(result.current).toEqual({ 算法工程师: 12 }));
  });

  it('岗位汇总批量获取并把失败岗位记为 null', async () => {
    const codes = ['AID-01', 'bad'];
    const { result } = renderHook(() => useJobSummaries(codes, true));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.summaries.get('AID-01')).toMatchObject({ job_code: 'AID-01' });
    expect(result.current.summaries.get('bad')).toBeNull();
  });

  it('匹配页 UI 状态是会话级单例并可重置', () => {
    const { result } = renderHook(() => useMatchUi());
    act(() => result.current.setUi({ stage: 'parsing', progress: 42 }));
    expect(result.current.ui.stage).toBe('parsing');
    expect(matchProgress()).toBe(42);
    act(() => result.current.resetUi());
    expect(result.current.ui.stage).toBe('upload');
    expect(matchProgress()).toBe(0);
  });

  it('完整 live 链路完成预检→抽取→匹配→学习路径，并保留熟练度档位', async () => {
    const { result } = renderHook(() => useLiveMatch());
    act(() => result.current.reset());
    const file = new File(['resume'], 'resume.pdf', { type: 'application/pdf' });
    await act(async () => { await result.current.analyze(file, 'AID-01'); });
    expect(result.current.run.phase).toBe('done');
    expect(result.current.run.candidateId).toBe('C1');
    expect(result.current.run.resumeText).toContain('Python');
    expect(result.current.run.levels).toEqual({ A: 'P3' });
    expect(api.preflightResume).toHaveBeenCalledOnce();
    expect(api.extractCandidate).toHaveBeenCalledWith(file, undefined, expect.any(AbortSignal), false);
    expect(api.runMatch).toHaveBeenCalledWith(expect.objectContaining({ job_code: 'AID-01', auto_proficiency: true, proficiency_scope: 'candidate' }), expect.any(AbortSignal));
    expect(api.runLearningPath).toHaveBeenCalledWith(expect.objectContaining({ proficiency_levels: { A: 'P3' }, auto_proficiency: false }), expect.any(AbortSignal));
  });

  it('低质量预检先停在 quality_hold，确认后才允许进入模型抽取', async () => {
    api.preflightResume.mockResolvedValueOnce({ quality: { fallback_required: true, reason: '扫描件质量低' } });
    const { result } = renderHook(() => useLiveMatch());
    act(() => result.current.reset());
    const file = new File(['scan'], 'scan.pdf', { type: 'application/pdf' });
    await act(async () => { await result.current.analyze(file, 'AID-01'); });
    expect(result.current.run.phase).toBe('quality_hold');
    expect(api.extractCandidate).not.toHaveBeenCalled();
    await act(async () => { await result.current.proceedAnyway(file, 'AID-01'); });
    expect(result.current.run.phase).toBe('done');
    expect(result.current.run.lowQualityAccepted).toBe(true);
    expect(api.extractCandidate).toHaveBeenCalledWith(file, undefined, expect.any(AbortSignal), true);
  });

  it('改选岗位复用已抽取 profile 与熟练度，不重复抽取和自动定级', async () => {
    const { result } = renderHook(() => useLiveMatch());
    act(() => result.current.reset());
    const file = new File(['resume'], 'resume.pdf', { type: 'application/pdf' });
    await act(async () => { await result.current.analyze(file, 'AID-01'); });
    api.extractCandidate.mockClear();
    api.runMatch.mockClear();
    await act(async () => { await result.current.recompute('AID-02'); });
    expect(result.current.run.phase).toBe('done');
    expect(result.current.run.jobCode).toBe('AID-02');
    expect(api.extractCandidate).not.toHaveBeenCalled();
    expect(api.runMatch).toHaveBeenCalledWith(expect.objectContaining({ job_code: 'AID-02', proficiency_levels: { A: 'P3' }, auto_proficiency: false, proficiency_scope: 'candidate' }), expect.any(AbortSignal));
  });

  it('链路失败时记录失败阶段和错误信息，并可 reset 回 idle', async () => {
    api.preflightResume.mockRejectedValueOnce(new Error('文件解析失败'));
    const { result } = renderHook(() => useLiveMatch());
    act(() => result.current.reset());
    const file = new File(['bad'], 'bad.pdf');
    await act(async () => { await result.current.analyze(file, 'AID-01'); });
    expect(result.current.run.phase).toBe('error');
    expect(result.current.run.failedPhase).toBe('preflight');
    expect(result.current.run.error).toBe('文件解析失败');
    act(() => result.current.reset());
    expect(result.current.run.phase).toBe('idle');
  });

});

