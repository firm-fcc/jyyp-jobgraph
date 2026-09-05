import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.resetModules();
});

describe('matchApi HTTP 客户端', () => {
  it('规范化后端地址并发送 GET 请求与查询参数', async () => {
    vi.stubEnv('VITE_MATCH_API', 'http://127.0.0.1:8000///');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const api = await import('@/api/matchApi');
    expect(api.MATCH_API).toBe('http://127.0.0.1:8000');
    expect(api.LIVE_MATCH).toBe(true);
    await api.fetchJdList('算法/工程师', 12);
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8000/api/jobs?std_job=%E7%AE%97%E6%B3%95%2F%E5%B7%A5%E7%A8%8B%E5%B8%88&limit=12');
  });

  it('HTTP JSON 错误转成 MatchApiError 并保留 detail', async () => {
    vi.stubEnv('VITE_MATCH_API', 'http://api');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '岗位不存在' }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    })));
    const api = await import('@/api/matchApi');
    await expect(api.fetchJobSummary('AID-01')).rejects.toMatchObject({ status: 404, detail: '岗位不存在', name: 'MatchApiError' });
  });

  it('非 JSON 错误退回 HTTP 状态码', async () => {
    vi.stubEnv('VITE_MATCH_API', 'http://api');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('bad gateway', { status: 502 })));
    const api = await import('@/api/matchApi');
    await expect(api.fetchHealth()).rejects.toMatchObject({ status: 502, detail: 'HTTP 502' });
  });

  it('runMatch / learningPath 发送 JSON POST', async () => {
    vi.stubEnv('VITE_MATCH_API', 'http://api');
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })));
    vi.stubGlobal('fetch', fetchMock);
    const api = await import('@/api/matchApi');
    const body = { candidate_profile: { candidate_id: 'c1' }, job_code: 'AID-01' } as any;
    await api.runMatch(body);
    await api.runLearningPath(body);
    expect(fetchMock).toHaveBeenNthCalledWith(1, 'http://api/api/match', expect.objectContaining({
      method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' },
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, 'http://api/api/learning-path', expect.objectContaining({ method: 'POST' }));
  });

  it('预检与抽取用 FormData 上传文件，并支持低质量解析开关', async () => {
    vi.stubEnv('VITE_MATCH_API', 'http://api');
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ok: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })));
    vi.stubGlobal('fetch', fetchMock);
    const api = await import('@/api/matchApi');
    const file = new File(['resume'], 'resume.txt', { type: 'text/plain' });
    await api.preflightResume(file);
    await api.extractCandidate(file, 'candidate-1', undefined, true);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api/api/candidate?preflight=true');
    expect(fetchMock.mock.calls[1][0]).toBe('http://api/api/candidate?allow_low_quality_parser=true');
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: 'POST', body: expect.any(FormData) }));
  });
});

describe('client 取数适配层', () => {
  it('未配置 API_BASE 时从内存数据集返回图谱与棱镜', async () => {
    vi.stubEnv('VITE_API_BASE', '');
    vi.doMock('@/data/generator', () => ({ getDataset: () => ({ nodes: [1], edges: [2], prismTimeline: { x: 1 } }) }));
    const client = await import('@/api/client');
    expect(client.IS_MOCK).toBe(true);
    expect(client.useDataset()).toMatchObject({ nodes: [1], edges: [2] });
    await expect(client.fetchGraph()).resolves.toEqual({ nodes: [1], edges: [2] });
    await expect(client.fetchPrismTimeline()).resolves.toEqual({ x: 1 });
  });

  it('配置 API_BASE 后走远端接口并处理错误', async () => {
    vi.stubEnv('VITE_API_BASE', 'http://graph-api');
    vi.doMock('@/data/generator', () => ({ getDataset: () => ({}) }));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ nodes: [], edges: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response('fail', { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);
    const client = await import('@/api/client');
    expect(client.IS_MOCK).toBe(false);
    await expect(client.fetchGraph()).resolves.toEqual({ nodes: [], edges: [] });
    await expect(client.fetchPrismTimeline()).rejects.toThrow('prism/monthly 500');
  });
});
