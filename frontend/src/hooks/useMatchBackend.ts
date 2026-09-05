/* ============================================================
   人岗匹配后端的会话状态

   一次诊断分四步，各自可能失败，且耗时相差两个数量级：

     1 预检   解析简历版面，不调用大模型，百毫秒级
     2 抽取   冻结的 Candidate 管线，逐段调用大模型，分钟级
     3 匹配   确定性比对，毫秒级
     4 路径   确定性规划，毫秒级

   抽取一步最慢也最贵，因此它的产物（candidate_skill_profile）在会话内留存：
   报告页切换基准 JD 时只重跑第 3、4 步，不重新抽取。

   自动定级（auto_proficiency）需要另一轮大模型调用。后端未配置模型时该步失败，
   此时以不定级的方式重跑一次：已支持但无法定级的能力将落入“证据不足”，
   而不是让整次诊断失败。这一回落必须在界面上说明，不能默默降级。

   预检不过关时链路在此停住（quality_hold），不自动往下走。后端默认拒绝把低质量
   文本送入模型，这一道门是对的：版面还原失败的简历抽不出可核验的证据，
   跑完只会得到一份看似有据、实则错位的报告。是否越过这道门由使用者决定，
   前端不代为选择，也不静默放行。
   ============================================================ */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  extractCandidate,
  fetchHealth,
  fetchJdList,
  fetchJobIndex,
  fetchJobSummary,
  LIVE_MATCH,
  MatchApiError,
  preflightResume,
  runLearningPath,
  runMatch,
  type CandidatePreflight,
  type CandidateSkillProfile,
  type ExplicitSkillMention,
  type HealthResponse,
  type JdItem,
  type JobSummaryResponse,
  type LearningPathApiResponse,
  type MatchingApiResponse,
  type MatchRequestBody,
  type ProficiencyLevel,
} from '@/api/matchApi';

/** 后端可达性。off 表示没配地址，本页整体走演示链路 */
export type BackendStatus = 'off' | 'probing' | 'ready' | 'unreachable';

/** 一次诊断走到了哪一步。quality_hold 是预检不过关后的等待，须由使用者决定去留 */
export type RunPhase =
  | 'idle'
  | 'preflight'
  | 'quality_hold'
  | 'extracting'
  | 'matching'
  | 'planning'
  | 'done'
  | 'error';

export interface LiveRun {
  phase: RunPhase;
  /** 当前步骤的说明，直接显示在解析页 */
  note: string;
  error: string | null;
  /** 出错时保留失败发生在哪一步。phase 转入 error 后，原步骤即由此处取得 */
  failedPhase: RunPhase | null;
  preflight: CandidatePreflight | null;
  candidateId: string | null;
  profile: CandidateSkillProfile | null;
  /** 解析后的简历全文。证据的字符偏移落在它上面，左栏据此就地标注 */
  resumeText: string;
  /** 简历技能清单里列到的技术名。只作展示，不构成对能力的支撑 */
  mentions: ExplicitSkillMention[];
  match: MatchingApiResponse | null;
  path: LearningPathApiResponse | null;
  /** 本次比对的标准岗位编码（AID-01）。基准是该岗位窗口内全部招聘信息的汇总 */
  jobCode: string | null;
  /* 简历已具备的各项能力的熟练度档位。定级逐项调模型、一项数十秒，故在第一次
     比对时对全部已具备能力一次算齐；此后改选目标岗位只把它随请求带上，
     后端不再调模型，比对即时返回。 */
  levels: Record<string, ProficiencyLevel>;
  /** 自动定级失败后以不定级方式重跑过 */
  proficiencyFallback: boolean;
  /** 自动定级失败的原因，用于界面说明 */
  proficiencyNote: string | null;
  /** 本次已越过解析质量门 */
  lowQualityAccepted: boolean;
}

const EMPTY: LiveRun = {
  phase: 'idle',
  note: '',
  error: null,
  failedPhase: null,
  preflight: null,
  candidateId: null,
  profile: null,
  resumeText: '',
  mentions: [],
  match: null,
  path: null,
  jobCode: null,
  levels: {},
  proficiencyFallback: false,
  proficiencyNote: null,
  lowQualityAccepted: false,
};

const msg = (e: unknown) =>
  e instanceof MatchApiError ? e.detail : e instanceof Error ? e.message : String(e);

/** 后端可达性探测。只在挂载时跑一次 */
export function useBackendHealth() {
  const [status, setStatus] = useState<BackendStatus>(LIVE_MATCH ? 'probing' : 'off');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [detail, setDetail] = useState<string | null>(null);

  useEffect(() => {
    if (!LIVE_MATCH) return;
    const ctrl = new AbortController();
    fetchHealth(ctrl.signal)
      .then((h) => {
        setHealth(h);
        setStatus('ready');
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setDetail(msg(e));
        setStatus('unreachable');
      });
    return () => ctrl.abort();
  }, []);

  return { status, health, detail };
}

/** 某个岗位在后端窗口下的 JD 列表 */
export function useJdList(stdJob: string | null, enabled: boolean) {
  const [items, setItems] = useState<JdItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /* 手上这批 JD 属于哪个岗位。换岗位之后新列表到达之前，items 里仍是上一个岗位的
     招聘信息；调用方若不知道这一点，就会拿别的岗位的 JD 当本岗位的比对基准。 */
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !stdJob) {
      setItems([]);
      setTotal(0);
      setError(null);
      setLoadedFor(null);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    fetchJdList(stdJob, 40, ctrl.signal)
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
        setLoadedFor(stdJob);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setItems([]);
        setTotal(0);
        setLoadedFor(null);
        setError(msg(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [stdJob, enabled]);

  return { items, total, loading, error, loadedFor };
}

/**
 * 本窗口内有招聘信息样本的岗位名及其条数。
 *
 * 取岗位汇总之前先取这一份：本批 131 个岗位里，窗口内有样本的是 97 个，
 * 其余岗位后端一律回 404。照着图谱的岗位表逐个去问，控制台会留下三十余条
 * 404，评审时看上去像是接口坏了，实则是该岗位这个月没有招聘信息。
 */
export function useJobIndex(enabled: boolean) {
  const [counts, setCounts] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    fetchJobIndex(ctrl.signal)
      .then((r) => setCounts(r.counts))
      /* 取不到就不过滤，让下游照旧按岗位表去问；那一路本就容错 */
      .catch(() => setCounts(null));
    return () => ctrl.abort();
  }, [enabled]);

  return counts;
}

/* ---------------- 岗位能力要求的窗口汇总 ----------------

   报告页里凡是要在岗位之间比较的读数 —— 岗位选择器的覆盖度、能力地形的定位、
   相近岗位的相似度与覆盖 —— 都要一份「这个岗位到底要什么能力、各要到什么份上」。
   服务端按窗口内全部招聘信息汇总出的提及率即是，见 /api/job-summary。

   按岗位逐个取，取到即入表；本窗口内没有样本的岗位后端回 404，记一个空位，
   调用方据此退回图谱一侧的权重，不再重试。一份约二十千字节，全站九十七个
   岗位合计两兆上下，故在报告页首次进入实测链路时一次性取齐，之后不再请求。 */

/** jobCode → 该岗位的能力要求汇总。值为 null 表示后端本窗口内没有该岗位 */
export type JobSummaryMap = Map<string, JobSummaryResponse | null>;

/**
 * 批量取岗位能力要求汇总。
 *
 * codes 为空或未启用时不发请求。同一批 code 只取一次：报告页每换一次目标岗位
 * 都会重算派生量，若跟着重取一遍，切岗位就要等两兆数据落地。
 */
export function useJobSummaries(codes: string[], enabled: boolean) {
  const [map, setMap] = useState<JobSummaryMap>(new Map());
  const [loading, setLoading] = useState(false);
  /* 已取过的 code 跨渲染留存：codes 每次渲染都是新数组，靠它判断是否真的换了一批 */
  const done = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!enabled || codes.length === 0) return;
    const todo = codes.filter((c) => !done.current.has(c));
    if (todo.length === 0) return;
    for (const c of todo) done.current.add(c);

    const ctrl = new AbortController();
    setLoading(true);
    /* 并发但分批：九十七个请求一次全发出去，浏览器自己会排队，
       而中途换页时未发出的那些也无从取消。每批八个，取完一批再发下一批。 */
    (async () => {
      const got: [string, JobSummaryResponse | null][] = [];
      for (let i = 0; i < todo.length; i += 8) {
        if (ctrl.signal.aborted) return;
        const batch = todo.slice(i, i + 8);
        const rs = await Promise.all(
          batch.map((c) =>
            fetchJobSummary(c, ctrl.signal)
              .then((r) => [c, r] as [string, JobSummaryResponse | null])
              /* 404 是「本窗口内没有这个岗位」，不是故障；其余错误同样记空位，
                 调用方一律退回图谱权重，页面上不因此出现空白或报错。 */
              .catch(() => [c, null] as [string, JobSummaryResponse | null]),
          ),
        );
        got.push(...rs);
      }
      if (ctrl.signal.aborted) return;
      setMap((prev) => {
        const next = new Map(prev);
        for (const [c, r] of got) next.set(c, r);
        return next;
      });
      setLoading(false);
    })();

    return () => ctrl.abort();
  }, [codes, enabled]);

  return { summaries: map, loading };
}

/** 缺模型配置或输入不合规时，退回不定级重跑；其余错误照常抛出 */
const isProficiencyIssue = (e: unknown) =>
  e instanceof MatchApiError && (e.status === 502 || e.status === 400);

/* ---------------- 会话级单例 ----------------

   一轮解析要跑数分钟，而这一页在解析途中是可以离开的：读者会去别的页面看一眼
   再回来。此前这份状态挂在组件的 state 上，卸载时另把请求 abort 掉 —— 离开一次
   等于把整轮解析连同已到手的结果一并丢掉，回来只剩一张空的上传页。

   现把状态与运行中的请求提到模块一级：组件订阅它，卸载不打断请求，回到本页
   时接着上一次的进度往下显示。整站一次只跑一轮解析，故取单例而非按实例分配。
   显式重置（换一份简历重来）才中断请求，见 reset。 */
let RUN: LiveRun = EMPTY;
let CTRL: AbortController | null = null;
const SUBS = new Set<() => void>();

function pushRun(next: LiveRun | ((s: LiveRun) => LiveRun)) {
  RUN = typeof next === 'function' ? (next as (s: LiveRun) => LiveRun)(RUN) : next;
  for (const f of SUBS) f();
}

/* ---------------- 这一页的呈现状态 ----------------

   走到哪一屏、这一轮走的哪条链路、上传件是什么，三项与上面那份结果同去同留：
   只把结果留下而让页面退回上传屏，等于结果还在却没有入口去看。 */
export type MatchStage = 'upload' | 'parsing' | 'report';

interface MatchUi {
  stage: MatchStage;
  runMode: 'demo' | 'live';
  file: File | null;
  fileName: string | null;
  progress: number;
  /** 报告已出过一次：上传屏据此给出"返回报告"的入口 */
  hasReport: boolean;
}

const UI_EMPTY: MatchUi = {
  stage: 'upload',
  runMode: 'demo',
  file: null,
  fileName: null,
  progress: 0,
  hasReport: false,
};

let UI: MatchUi = UI_EMPTY;
const UI_SUBS = new Set<() => void>();

function pushUi(patch: Partial<MatchUi>) {
  UI = { ...UI, ...patch };
  for (const f of UI_SUBS) f();
}

/** 进度条的当前值。函数式更新要读它，而单例的读取不经过 React */
export const matchProgress = () => UI.progress;

/** 这一页的呈现状态。离开本页不清空，回来时接着上次那一屏 */
export function useMatchUi() {
  const ui = useSyncExternalStore(
    (cb) => {
      UI_SUBS.add(cb);
      return () => UI_SUBS.delete(cb);
    },
    () => UI,
  );
  return { ui, setUi: pushUi, resetUi: () => pushUi(UI_EMPTY) };
}

export function useLiveMatch() {
  const run = useSyncExternalStore(
    (cb) => {
      SUBS.add(cb);
      return () => SUBS.delete(cb);
    },
    () => RUN,
  );
  const setRun = pushRun;
  const ctrlRef = { get current() { return CTRL; }, set current(v: AbortController | null) { CTRL = v; } };

  const reset = useCallback(() => {
    CTRL?.abort();
    CTRL = null;
    pushRun(EMPTY);
  }, []);

  /**
   * 第 3、4 步：比对与路径规划。抽取产物已在手时（改选目标岗位）单独复用。
   *
   * known 是上一次算好的熟练度档位。给了它就把它带上并关掉自动定级 —— 后端不再
   * 调模型，比对与规划都是纯规则运算，改选岗位因而即时可得；没给（本轮第一次）
   * 才开自动定级，且按 candidate 范围一次给简历已具备的全部能力算齐。
   */
  const computeAgainst = useCallback(
    async (
      profile: CandidateSkillProfile,
      jobCode: string,
      signal: AbortSignal,
      known?: Record<string, ProficiencyLevel>,
    ): Promise<
      Pick<LiveRun, 'match' | 'path' | 'levels' | 'proficiencyFallback' | 'proficiencyNote'>
    > => {
      const base: MatchRequestBody = { candidate_profile: profile, job_code: jobCode };
      const reuse = known && Object.keys(known).length > 0;
      let fallback = false;
      let note: string | null = null;

      let matched: MatchingApiResponse;
      if (reuse) {
        /* 带的是候选人全量档位，故声明 candidate 范围：服务端据此把它收窄到
           这个岗位真正要比等级的那几项，其余项照其有无证据判定。 */
        matched = await runMatch(
          {
            ...base,
            proficiency_levels: known,
            auto_proficiency: false,
            proficiency_scope: 'candidate',
          },
          signal,
        );
      } else {
        try {
          matched = await runMatch(
            { ...base, auto_proficiency: true, proficiency_scope: 'candidate' },
            signal,
          );
        } catch (e) {
          if (signal.aborted || !isProficiencyIssue(e)) throw e;
          fallback = true;
          note = msg(e);
          matched = await runMatch({ ...base, auto_proficiency: false }, signal);
        }
      }

      /* 比对已出结果，转入规划一步。两步分开报，失败时才落得到具体一步上 */
      if (!signal.aborted) {
        setRun((s) => ({ ...s, phase: 'planning', note: '正在按比对结果规划学习路径…' }));
      }

      /* 定级结果沿用到路径规划，避免第二次调用大模型给出不同的等级 */
      const levels = (known ?? matched.proficiency?.levels ?? {}) as Record<string, ProficiencyLevel>;
      const pathBody: MatchRequestBody = fallback
        ? { ...base, auto_proficiency: false }
        : {
            ...base,
            proficiency_levels: levels,
            auto_proficiency: false,
            proficiency_scope: 'candidate',
          };
      const path = await runLearningPath(pathBody, signal);

      return { match: matched, path, levels, proficiencyFallback: fallback, proficiencyNote: note };
    },
    [],
  );

  /** 第 2 步起：抽取 → 匹配 → 路径。预检已过或使用者已决定放行时进入 */
  const runFrom2 = useCallback(
    async (file: File, jobCode: string, signal: AbortSignal, allowLowQuality: boolean) => {
      setRun((s) => ({
        ...s,
        phase: 'extracting',
        failedPhase: null,
        lowQualityAccepted: allowLowQuality,
        note: '正在抽取经历中的行为证据并对齐至能力体系，此步逐段调用模型，耗时较长…',
      }));

      const cand = await extractCandidate(file, undefined, signal, allowLowQuality);
      if (signal.aborted) return;
      setRun((s) => ({
        ...s,
        candidateId: cand.candidate_id,
        profile: cand.candidate_skill_profile,
        resumeText: cand.resume_text ?? '',
        mentions: cand.explicit_skill_mentions ?? [],
        phase: 'matching',
        /* 这一步的耗时几乎全在定级上：比对本身是纯规则运算，瞬时完成，
           而简历已具备的每一项能力都要单独送模型判定档位。文案照此写，
           久候时才对得上后台实际在做的事。 */
        note: '正在逐项判定已具备能力的熟练度档位，此步逐项调用模型…',
      }));

      const out = await computeAgainst(cand.candidate_skill_profile, jobCode, signal);
      if (signal.aborted) return;
      setRun((s) => ({ ...s, ...out, phase: 'done', note: '' }));
    },
    [computeAgainst],
  );

  /** 整条链路：预检 →（不过关则停）→ 抽取 → 匹配 → 路径 */
  const analyze = useCallback(
    async (file: File, jobCode: string) => {
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      const { signal } = ctrl;

      setRun({ ...EMPTY, phase: 'preflight', note: '正在解析简历版面…', jobCode });
      try {
        const pre = await preflightResume(file, signal);
        if (signal.aborted) return;

        if (pre.quality.fallback_required) {
          /* 停在这里等使用者决定。此步尚未调用模型，不产生任何费用 */
          setRun((s) => ({ ...s, preflight: pre, phase: 'quality_hold', note: '' }));
          return;
        }
        setRun((s) => ({ ...s, preflight: pre }));
        await runFrom2(file, jobCode, signal, false);
      } catch (e) {
        if (signal.aborted) return;
        setRun((s) => ({ ...s, phase: 'error', failedPhase: s.phase, error: msg(e), note: '' }));
      }
    },
    [runFrom2],
  );

  /** 使用者看过预检结果后决定继续。风险已在界面上说明 */
  const proceedAnyway = useCallback(
    async (file: File, jobCode: string) => {
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      const { signal } = ctrl;
      try {
        await runFrom2(file, jobCode, signal, true);
      } catch (e) {
        if (signal.aborted) return;
        setRun((s) => ({ ...s, phase: 'error', failedPhase: s.phase, error: msg(e), note: '' }));
      }
    },
    [runFrom2],
  );

  /**
   * 改选目标岗位后重算，不重新抽取。
   *
   * 熟练度档位在本轮第一次比对时已一次算齐，此处带着它走，后端不再调模型，
   * 因而是即时的 —— 不必把使用者送回解析屏干等。
   */
  const recompute = useCallback(
    async (jobCode: string) => {
      const profile = run.profile;
      if (!profile) return;
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      const { signal } = ctrl;

      setRun((s) => ({
        ...s,
        phase: 'matching',
        note: '正在按该岗位的能力要求重新比对…',
        error: null,
        failedPhase: null,
        jobCode,
      }));
      try {
        const out = await computeAgainst(profile, jobCode, signal, run.levels);
        if (signal.aborted) return;
        setRun((s) => ({ ...s, ...out, phase: 'done', note: '' }));
      } catch (e) {
        if (signal.aborted) return;
        setRun((s) => ({ ...s, phase: 'error', failedPhase: s.phase, error: msg(e), note: '' }));
      }
    },
    [computeAgainst, run.profile, run.levels],
  );

  /* 卸载不再 abort：这一页是可以中途离开的，请求要继续跑完，
     结果留在上面那份单例里，回到本页即接着显示。 */

  return { run, analyze, proceedAnyway, recompute, reset };
}

