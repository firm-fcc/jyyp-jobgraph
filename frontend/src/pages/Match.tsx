/* =========================================================
   人岗匹配

   三步走完一次诊断：上传简历 → 简历解析 → 匹配报告。
   解析跑完，进度条推到第三步，整条流程栏淡出收起，报告接上来。

   报告页是“左简历 · 右分析”的对读版式：
   左边常驻简历原文，右边是分析结果，两边双向联动 ——
   右边点一项能力，左边就高亮它是从哪一行抽出来的；
   左边点一行原文，右边就告诉你这一行支撑了哪些能力。
   只给分析结果、不给简历，看的人没法判断结论是不是站得住。

   右边的顺序也是先结论后依据：匹配度与改进建议在最上面，
   紧接着是真实性核验与经历关联度（这两件事决定前面的分数可不可信），
   再往下才是能力差距、地形、学习路径与相近岗位。

   本页有两条取数链路，但只有一套版式：

     实测链路  VITE_MATCH_API 指向的 backend/。真实简历经冻结的抽取管线产出
               能力画像，与该岗位窗口内的招聘信息汇总逐项比对，得到达成率、
               差距明细与学习路径，逐项判定全由服务端算出，不经前端加工。
     内置链路  未配置后端地址、后端不可达、或载入了本站内置示例简历时的形态。
               同一套判定口径改在前端算出，见 data/demoLive.ts；岗位一侧的要求
               取图谱的岗位定义（招聘汇总表的覆盖率与平均档位，均为实测），
               学习路径取 public/data/devgraph.json，与服务端所用的是同一批
               能力发展图谱。

   两条链路填的是同一组视图模型（matchLive 的 ReportModel），此后走同一条渲染
   路径。此前内置链路另有一套五维加权的版式与读数，同一页在两种情形下长得不
   一样，读者无从判断哪一处是口径之别、哪一处是实现之别，故并为一套。

   报告内服务端未覆盖的四块（岗位核心任务的覆盖、经历关联度、能力地形、相近岗位
   的能力覆盖）由逐项判定与图谱的岗位—任务—能力权重合成，见
   data/matchLiveDerived.ts；真实性核验的七项判据落在简历全文、逐项判定与该岗位
   招聘原文上。各块口径由一行说明交代（.rp-note）。
   ========================================================= */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useDataset } from '@/api/client';
import { Icon } from '@/components/Icon';
import { Footer } from '@/components/Footer';
import { PageGuide } from '@/components/common/PageGuide';
import { NextSteps, type StepItem } from '@/components/common/NextSteps';
import { stationOf } from '@/data/journey';
import { CareerTerrain } from '@/components/viz/CareerTerrain';
import { projectJobs, projectResume } from '@/data/matching';
import type { Distribution, GraphNode } from '@/types/graph';
import { MATCH_WINDOW, QUALITY_FLAG_TEXT } from '@/api/matchApi';
import {
  useBackendHealth,
  useJobIndex,
  useJobSummaries,
  useLiveMatch,
  useMatchUi,
  matchProgress,
  type RunPhase,
} from '@/hooks/useMatchBackend';
import {
  buildEvidenceIndex,
  buildLiveAdvice,
  buildLiveItems,
  buildLivePath,
  excludedRequirements,
  GAP_TEXT,
  levelLabel,
  PATH_MODE_TEXT,
  requiredLabel,
  summarizeCandidate,
  type ReportModel,
} from '@/data/matchLive';
import { buildDemoReport } from '@/data/demoLive';
import { useDevGraphs } from '@/hooks/useDevGraphs';
import {
  graphReqVecOf,
  liveAuditResume,
  liveExperienceLinks,
  liveJobCoverage,
  liveSimilarJobs,
  liveSkillVector,
  liveTaskCoverage,
  makeReqVecOf,
  segmentResume,
  type LiveCheckLevel,
} from '@/data/matchLiveDerived';
import { LiveGapLedger, LivePathPlan, LiveScoreBar } from '@/components/panels/LiveMatchReport';
import { LiveResumeDoc } from '@/components/panels/LiveResumeDoc';
import '@/styles/match.css';
import '@/styles/matchLive.css';

/* ==================== 常量 ==================== */

type Stage = 'upload' | 'parsing' | 'report';

/** 报告的七个分节。次序见组件内的 SEC_ORDER */
type SecKey = 'advice' | 'gap' | 'path' | 'auth' | 'exp' | 'terrain' | 'near';

const STEPS = [
  { n: '01', title: '上传简历', desc: '读取 PDF / Word 原文' },
  { n: '02', title: '简历解析', desc: '抽取任务与技能要素' },
  { n: '03', title: '匹配报告', desc: '真实性核验与差距分析' },
];

/** 解析阶段的四道工序 —— 对应算法侧 Extractor → Normalizer → Aligner → Matcher */
const PIPELINE: { title: string; icon: string; until: number }[] = [
  { title: '简历版面还原', icon: 'doc', until: 25 },
  { title: '任务与技能抽取', icon: 'spark', until: 50 },
  { title: '技能点归一对齐', icon: 'layers', until: 75 },
  { title: '多维度匹配计算', icon: 'target', until: 100 },
];

/** 实测链路的四道工序，与 backend/ 的四步一一对应，进度上限同 LIVE_CEIL */
const LIVE_PIPELINE: { title: string; icon: string; until: number }[] = [
  { title: '简历版面解析', icon: 'doc', until: 22 },
  { title: '行为证据抽取与能力判定', icon: 'spark', until: 82 },
  { title: '岗位要求逐项比对', icon: 'target', until: 96 },
  { title: '学习路径规划', icon: 'route', until: 100 },
];

const STATUS = [
  '正在还原简历版面，识别分段与项目经历…',
  '正在抽取项目经历中的任务描述与技能实体…',
  '正在合并同义技能，归一到图谱的技能点层…',
  '正在比对目标岗位的任务与能力要求，计算差距…',
];
/** 跑满之后到报告接上来之间还有半秒，文案不能停在“正在…”上 */
const STATUS_DONE = '解析完成，正在生成匹配报告…';

/**
 * 会长时间调用模型的两步，及其耗时预期。列在此处的步骤在小字里附已用时。
 *
 * 其余步骤为纯规则运算，瞬时完成，附上已用时反倒像是在等什么。区间取自实测：
 * 推理型模型同一份输入的输出量本就可差十余倍，抽取一步内又有一次面向全篇的
 * 语义召回无法拆分并发，故只给区间，不给单值。
 */
const LIVE_MODEL_PHASE_HINT: Record<string, string> = {
  extracting: '这一步通常需要五到十分钟',
  matching: '这一步通常需要一到两分钟',
};

const topKey = (dist?: Distribution) =>
  dist ? (Object.entries(dist).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '—') : '—';

const CHECK_TEXT: Record<LiveCheckLevel, string> = { pass: '通过', watch: '存疑', risk: '风险' };

const EXP_KIND: Record<string, string> = {
  work: '工作',
  project: '项目',
  competition: '竞赛',
  research: '科研',
};

/* ==================== 页面 ==================== */

export function Match() {
  const d = useDataset();
  const nav = useNavigate();
  const [params] = useSearchParams();

  const jobs = useMemo(() => d.nodes.filter((n) => n.kind === 'job'), [d.nodes]);
  const jobIds = useMemo(() => jobs.map((j) => j.id), [jobs]);
  const jobById = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);

  const linked = params.get('target');
  const fromJobs = !!(linked && jobById.has(linked));

  /* 走到哪一屏、上传件是什么、进度到哪，四项挂在模块级的单例上而不是组件
     state 上：这一页的一轮解析要跑数分钟，中途去别的页面看一眼再回来是常事，
     挂在组件上则一离开即清空，回来只剩一张空的上传屏。见 useMatchUi。 */
  const { ui, setUi } = useMatchUi();
  const { stage, progress, fileName, file, runMode } = ui;
  const setStage = useCallback((v: Stage) => setUi({ stage: v, ...(v === 'report' ? { hasReport: true } : {}) }), [setUi]);
  const setProgress = useCallback(
    (v: number | ((p: number) => number)) =>
      setUi({ progress: typeof v === 'function' ? v(matchProgress()) : v }),
    [setUi],
  );
  const setFileName = useCallback((v: string | null) => setUi({ fileName: v }), [setUi]);
  const setFile = useCallback((v: File | null) => setUi({ file: v }), [setUi]);
  const setRunMode = useCallback((v: 'demo' | 'live') => setUi({ runMode: v }), [setUi]);
  const [leaving, setLeaving] = useState(false);
  const [dragging, setDragging] = useState(false);
  /* 示例简历默认不选中：这一页的正路是上传真实简历，示例只是没有简历在手时的
     备选。默认亮着一枚 chip 会让人以为已经选好了一份，上传区反倒成了可选项。
     -1 表示尚未选择；报告与预览仍按第一份取数（见 resume），只是选择器不亮。 */
  const [resumeIdx, setResumeIdx] = useState(-1);
  const [query, setQuery] = useState('');
  const [toast, setToast] = useState<string | null>(null);

  const [targetId, setTargetId] = useState<string>(() => {
    if (linked) return linked;
    const stable = jobs.filter((j) => !j.emerging);
    return (stable.length ? stable : jobs).sort(
      (a, b) => (b.attrs?.postCount ?? 0) - (a.attrs?.postCount ?? 0),
    )[0]?.id;
  });

  const resume = d.resumes[Math.max(0, resumeIdx)];
  const targetJob = jobById.get(targetId) ?? jobs[0];

  /* ---------------- 后端 ----------------
     可达性只探一次；JD 列表随目标岗位变；诊断链路的状态归 useLiveMatch 管。 */
  const backend = useBackendHealth();
  const backendReady = backend.status === 'ready';
  const { run, analyze, proceedAnyway, recompute, reset: resetLive } = useLiveMatch();

  /* 本窗口内有招聘信息样本的岗位。没有样本的岗位无从比对，选中它只能走演示链路，
     故在选择器里就要能判断，不能等到按下开始解析。 */
  const jobIndex = useJobIndex(backendReady);

  /* 本次报告是否由后端算出。内置链路下为 false。版式两者一致，此处只用于决定
     从哪一侧取数（见 report），以及左栏页眉给的是文件名还是示例简历名。

     重算途中（改选目标岗位后的 matching / planning 两步）也算实测：手上还留着
     上一轮的实测结果，此时若判作非实测，整页会切成演示版式 —— 布局与读数全变，
     一秒后再换回来，看上去像是页面出了错。改为留在实测版式上，读数旁另有一行
     进行时说明正在重算，见 .rp-base-meta.busy。 */
  const live = !!run.match && ['done', 'matching', 'planning'].includes(run.phase);

  const say = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 2600);
  };

  /* ---------------- 解析进度 ----------------
     刻意用 setInterval 而不是 requestAnimationFrame：
     rAF 在不合成帧的预览环境里根本不触发，动效会整个哑掉。

     演示链路的进度是匀速走完的动画；实测链路的进度由后端所处的步骤决定 ——
     抽取一步要逐段调用模型，耗时以分钟计，不能用定长动画冒充。
     每一段内部仍缓慢爬升，但爬不过该段的上限，跨段要等后端真的走到下一步。 */

  /* 本次诊断走哪条链路，在点"开始解析"的一刻定下，中途不变。
     与上面几项同存于 useMatchUi 的单例里。 */

  /* 抽取与定级各是一个长请求：前端发出去之后要等后端把该步的模型调用全部
     跑完才拿得到回应，中途没有可读的进度，进度条因而顶在这一段的上限上不动。
     两步合计常在十分钟上下，界面上只有一条静止的进度条时，这十分钟与“卡死了”
     看不出分别，故另给一个自解析开始起算的已用时，两步都附。 */
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (stage !== 'parsing') {
      setElapsed(0);
      return;
    }
    const t0 = Date.now();
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [stage]);
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  useEffect(() => {
    if (stage !== 'parsing' || runMode !== 'demo') return;
    /* 起点接着上次，不从零重来：这一页中途离开再回来时组件重新挂载，
       此处若归零，一条已经走到一半的进度条会当着人的面倒退回去。
       归零改在按下"开始解析"的那一刻做（见 startRun）。 */
    let p = matchProgress();
    const id = window.setInterval(() => {
      p = Math.min(100, p + 2);
      setProgress(p);
      if (p >= 100) window.clearInterval(id);
    }, 44);
    return () => window.clearInterval(id);
  }, [stage, runMode]);

  /** 实测链路各步骤的进度上限，与 LIVE_PIPELINE 的四格边界对齐 */
  const LIVE_CEIL: Record<string, number> = {
    idle: 4,
    preflight: 22,
    quality_hold: 22,
    extracting: 82,
    matching: 96,
    planning: 99,
    done: 100,
  };

  useEffect(() => {
    if (stage !== 'parsing' || runMode !== 'live') return;
    /* 出错即就地冻住：进度停在失败的那一步上，不推到 100%，
       否则四格会全部判为已完成，与“未能完成”自相矛盾 */
    if (run.phase === 'error') return;
    const id = window.setInterval(() => {
      setProgress((p: number) => {
        const ceil = LIVE_CEIL[run.phase] ?? 4;
        if (run.phase === 'done') return 100;
        /* 越接近本段上限爬得越慢，不会顶在上限上闪动 */
        return p >= ceil ? p : Math.min(ceil, p + Math.max(0.4, (ceil - p) * 0.06));
      });
    }, 220);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, runMode, run.phase]);

  /* 跑满 100% 后：在第三步停一下让人看清，再让整条流程栏淡出，报告接上。
     实测链路还要等后端真的给出结果，否则报告会接在半份数据上。 */
  useEffect(() => {
    if (stage !== 'parsing' || progress < 100) return;
    if (runMode === 'live' && run.phase !== 'done') return;
    const a = window.setTimeout(() => setLeaving(true), 640);
    const b = window.setTimeout(() => {
      setStage('report');
      setLeaving(false);
      window.scrollTo({ top: 0, behavior: 'auto' });
    }, 1180);
    return () => {
      window.clearTimeout(a);
      window.clearTimeout(b);
    };
  }, [stage, progress, runMode, run.phase]);

  /** 目标岗位的标准编码。后端按它取该岗位窗口内全部招聘信息的汇总为基准 */
  const targetCode = targetJob?.id.replace(/^J:/, '') ?? null;

  /** 本次基准由多少条招聘信息汇总而来。取自服务端返回的基准本身，非前端估算 */
  const jdCount = useMemo(() => {
    const n = run.match?.target_job_profile.job.jd_count;
    return typeof n === 'number' ? n : null;
  }, [run.match]);

  /* 基准由多少条招聘信息汇总而来。实测链路取服务端返回的条数，内置链路取
     图谱岗位定义里的同一个量 —— 两者同出于该岗位窗口内的招聘汇总表。 */
  const baseCount = jdCount ?? targetJob?.jobDef?.n ?? null;

  /** 该岗位在后端窗口内有没有招聘信息。没有则无从比对，只能走演示链路 */
  const targetInWindow = !jobIndex || (!!targetJob && jobIndex[targetJob.name] !== undefined);

  /** 走哪条链路由三个条件同时决定：有真实上传件、后端可达、该岗位在窗口内有样本 */
  const canRunLive = !!(file && backendReady && targetCode && targetInWindow);

  /* 一次已知会落到某个岗位上的切换尚未落定。
     结果经模块级单例同步推给订阅者，而目标岗位是组件 state，要等下一次渲染
     才生效 —— 中间那一次渲染里岗位还是旧的，下面的重算若不拦住，
     会拿上一个岗位把刚到手的结果覆盖掉。 */
  const awaitJob = useRef<string | null>(null);

  /* 报告页改选了目标岗位：抽取产物仍可用，只把第 3、4 步重跑一遍。
     熟练度档位在本轮第一次比对时已一次算齐，此处带着走，后端不再调模型。 */
  useEffect(() => {
    if (stage !== 'report' || runMode !== 'live') return;
    if (!run.profile || !targetCode || !targetInWindow) return;
    if (awaitJob.current) {
      if (targetCode !== awaitJob.current) return;
      awaitJob.current = null;
    }
    if (run.jobCode === targetCode) return;
    void recompute(targetCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, runMode, run.profile, run.jobCode, targetCode, targetInWindow]);

  const startRun = (mode: 'demo' | 'live') => {
    setRunMode(mode);
    setProgress(0);
    if (mode === 'live' && file && targetCode) void analyze(file, targetCode);
    else resetLive();
    setStage('parsing');
  };

  /* ---------------- 实测链路的派生数据 ----------------
     后端与本站的技能层共用同一套身份（team_skill_id），逐项结论因而能挂回
     图谱节点，取到本站的规范名、定义与所属维度。 */

  const skillNodes = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of d.nodes) {
      if (n.kind !== 'skill') continue;
      m.set(n.id, n);
      /* 图谱的节点 id 带层前缀（S:T-AI-01），后端给的是裸的 team_skill_id，
         两种写法各登记一次，取数一侧就不必知道另一侧的命名习惯。 */
      const bare = n.id.replace(/^S:/, '');
      if (bare !== n.id) m.set(bare, n);
    }
    return m;
  }, [d.nodes]);

  const liveItems = useMemo(
    () =>
      run.match ? buildLiveItems(run.match.match_result, run.match.target_job_profile, skillNodes) : [],
    [run.match, skillNodes],
  );
  const liveExcluded = useMemo(
    () => (run.match ? excludedRequirements(run.match.target_job_profile, skillNodes) : []),
    [run.match, skillNodes],
  );

  /* ---------------- 两条链路的同一份取数 ----------------
     实测链路把服务端的逐项判定转成视图模型，内置链路按同一口径在前端算出。
     此后整页只读 report，不再分辨本次是哪一条链路 —— 分辨得出来的地方，
     版式就会跟着分叉。 */

  const devGraphs = useDevGraphs(stage === 'report');

  const demoReport = useMemo(
    () => buildDemoReport(resume, targetJob, d.edges, skillNodes, devGraphs),
    [resume, targetJob, d.edges, skillNodes, devGraphs],
  );

  const liveReport = useMemo<ReportModel | null>(() => {
    if (!live || !run.match) return null;
    const summary = run.match.match_result.summary;
    return {
      items: liveItems,
      counts: summary,
      score: run.match.match_result.match_score,
      advice: buildLiveAdvice(summary, liveItems),
      path: run.path ? buildLivePath(run.path, skillNodes) : null,
      own: liveSkillVector(
        run.profile ?? { candidate_id: '', skill_registry_version: '', assessments: [], metadata: {} },
        run.match.proficiency?.levels,
        skillNodes,
      ),
      resumeText: run.resumeText,
      mentions: run.mentions,
      evidence: buildEvidenceIndex(liveItems),
      summary: run.profile ? summarizeCandidate(run.profile) : null,
    };
  }, [live, run.match, run.path, run.profile, run.resumeText, run.mentions, liveItems, skillNodes]);

  const report = liveReport ?? demoReport;
  /** 右栏点了某项能力，左栏只留支撑它的证据 */
  const [liveFocus, setLiveFocus] = useState<string | null>(null);
  /** 右栏点了某条核验或某段经历，左栏按字符偏移就地定位 */
  const [liveSpans, setLiveSpans] = useState<{ label: string; spans: [number, number][] } | null>(null);

  /* ---------------- 由逐项判定合成的四块 ----------------
     逐项判定只落在能力这一层。任务覆盖、能力地形、相近岗位与经历关联度四块
     所需的另一半是图谱的岗位—任务—能力权重（本批为实测），两者相乘即得，
     见 data/matchLiveDerived.ts。 */

  /* 岗位一侧的能力要求，优先取服务端按窗口汇总的 /api/job-summary。
     进入报告页时对全部岗位取齐一次（一份约二十千字节），此后切岗位不再请求。
     只问窗口内有样本的那些岗位（见 useJobIndex）；其余岗位在 makeReqVecOf 内
     退回图谱权重，不发请求。服务不可达时整体退回图谱权重，同为实测。 */
  const summaryCodes = useMemo(() => {
    if (stage !== 'report') return [];
    return jobs
      .filter((j) => !jobIndex || jobIndex[j.name] !== undefined)
      .map((j) => j.id.replace(/^J:/, ''));
  }, [stage, jobs, jobIndex]);
  const { summaries } = useJobSummaries(summaryCodes, stage === 'report' && backendReady);

  /** 岗位要哪些能力、各要到什么份上。汇总未到齐时先按图谱算，到齐后自动重算 */
  const reqVecOf = useMemo(
    () => (summaries.size > 0 ? makeReqVecOf(summaries) : graphReqVecOf),
    [summaries],
  );

  /** 本简历对任一岗位的能力覆盖。与页首的达成率不同源，见 matchLiveDerived */
  const scoreOf = useMemo(() => {
    const cache = new Map<string, number>();
    return (jid: string) => {
      const v = cache.get(jid);
      if (v !== undefined) return v;
      const s = liveJobCoverage(jid, report.own, reqVecOf);
      cache.set(jid, s);
      return s;
    };
  }, [report.own, reqVecOf]);

  const liveSegments = useMemo(() => segmentResume(report.resumeText), [report.resumeText]);

  const liveTasks = useMemo(
    () => liveTaskCoverage(targetId, report.own, reqVecOf),
    [targetId, report.own, reqVecOf],
  );

  const liveExpLinks = useMemo(
    () => liveExperienceLinks(liveSegments, report.items, targetId),
    [liveSegments, report.items, targetId],
  );

  /** 简历一侧已判定的各项能力所属的维度。核验里的“能力主线”一项读它 */
  const liveProfileDims = useMemo(
    () =>
      report.items
        .filter((i) => i.gap !== 'MISSING')
        .map((i) => i.dimension ?? '未归类'),
    [report.items],
  );

  const liveAudit = useMemo(
    () =>
      liveAuditResume(
        report.resumeText,
        liveSegments,
        report.items,
        report.mentions,
        targetJob?.name ?? '',
        liveProfileDims,
      ),
    [report.resumeText, report.mentions, report.items, liveSegments, targetJob?.name, liveProfileDims],
  );

  /* “在 131 个岗位中排名第 N”整块撤下。
     排名要成立，前提是这 131 个分值彼此可比、且分得开。实测三份示例简历
     对全部 131 个岗位只产生 2–3 个不同分值（区间 0.17–0.24），
     那个名次实际上只是相同分值里的数组下标 —— 换个排序实现就会变。
     等岗位—能力映射与简历抽取词典落地、分值真正拉开之后再恢复。 */

  const coords = useMemo(() => projectJobs(jobIds), [jobIds]);
  const resumePos = useMemo(() => projectResume(jobIds, coords, scoreOf), [jobIds, coords, scoreOf]);

  const waypoints = useMemo(
    () =>
      report.items
        .filter((i) => i.gap !== 'SATISFIED')
        .slice(0, 3)
        .map((i) => ({ name: i.name, weight: i.weight })),
    [report.items],
  );

  /** 相近岗位：按要求向量的夹角相似度取最接近的几个 */
  const near = useMemo(
    () => liveSimilarJobs(targetId, jobIds, reqVecOf, 4),
    [targetId, jobIds, reqVecOf],
  );

  /** 报告页切换岗位用的下拉分组：顶部一组匹配度最高的，其余按岗位聚类分组 */
  const targetOptions = useMemo(() => {
    const byScore = [...jobs].sort((a, b) => scoreOf(b.id) - scoreOf(a.id));
    const top = byScore.slice(0, Math.min(8, byScore.length));
    const bag = new Map<string, typeof byScore>();
    for (const j of byScore) {
      const cl = j.cluster ?? '未归类';
      bag.set(cl, [...(bag.get(cl) ?? []), j]);
    }
    return {
      top,
      groups: [...bag.entries()]
        .map(([cluster, items]) => ({ cluster, items }))
        .sort((a, b) => scoreOf(b.items[0].id) - scoreOf(a.items[0].id)),
    };
  }, [jobs, scoreOf]);

  const filteredJobs = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = [...jobs].sort(
      (a, b) =>
        Number(b.id === linked) - Number(a.id === linked) || (b.attrs?.postCount ?? 0) - (a.attrs?.postCount ?? 0),
    );
    return q ? base.filter((j) => j.name.toLowerCase().includes(q) || (j.cluster ?? '').includes(q)) : base;
  }, [jobs, query, linked]);

  const step = stage === 'upload' ? 0 : progress < 100 ? 1 : 2;
  const stageIdx = Math.max(0, Math.min(PIPELINE.length - 1, PIPELINE.filter((p) => progress >= p.until).length));

  /* 实测链路的当前工序取自后端所处的步骤本身，不由进度反推 —— 进度是表征，步骤才是事实 */
  const LIVE_PIPE_IDX: Partial<Record<RunPhase, number>> = {
    idle: 0,
    preflight: 0,
    quality_hold: 0,
    extracting: 1,
    matching: 2,
    planning: 3,
    done: 3,
  };
  const livePipeIdx = LIVE_PIPE_IDX[run.phase] ?? 0;

  /** 出错时失败落在哪一格；未出错为 -1。失败前所处的步骤即失败的步骤 */
  const liveFailIdx =
    runMode === 'live' && run.phase === 'error' && run.failedPhase
      ? (LIVE_PIPE_IDX[run.failedPhase] ?? 0)
      : -1;

  /** 实测链路已完成的格数。同样取自步骤：末步跑完之前，第四格不计入 */
  const liveDoneCount = run.phase === 'done' ? LIVE_PIPELINE.length : livePipeIdx;

  /** 正在跑的那一道工序下面的一行小字。只说这一步在做什么，不解释口径 */
  const liveNote =
    runMode === 'live'
      ? run.phase === 'done'
        ? STATUS_DONE
        : LIVE_MODEL_PHASE_HINT[run.phase]
          ? `${run.note}（已用时 ${mmss(elapsed)}，${LIVE_MODEL_PHASE_HINT[run.phase]}）`
          : run.note
      : progress >= 100
        ? STATUS_DONE
        : STATUS[stageIdx];

  /* ---------------- 左右联动 ----------------
     右栏点一项能力或一条核验，左栏就地把它的落点标出来。两者都按字符偏移定位，
     与左栏渲染原文所用的是同一套偏移。 */

  const clearFocus = () => {
    setLiveFocus(null);
    setLiveSpans(null);
  };

  const showSpans = (label: string, spans: [number, number][]) => {
    setLiveFocus(null);
    setLiveSpans({ label, spans });
    if (spans.length === 0) say(`“${label}”未在简历原文中找到对应段落`);
  };

  /* ---------------- 核验与经历关联的呈现 ----------------
     两节的取数已在 report 上折成一份，此处只把它整成视图直接消费的形状。 */

  interface ShownCheck {
    id: string;
    title: string;
    level: LiveCheckLevel;
    metric: string;
    detail: string;
    items?: string[];
    /** 可回溯的原文处数，为 0 时不给“看原文” */
    origin: number;
    show: () => void;
  }

  const shownAudit = useMemo(
    () => ({
      score: liveAudit.score,
      risk: liveAudit.risk,
      watch: liveAudit.watch,
      checks: liveAudit.checks.map(
        (c): ShownCheck => ({
          ...c,
          origin: c.spans.length,
          show: () => showSpans(c.title, c.spans),
        }),
      ),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [liveAudit],
  );

  interface ShownExp {
    id: string;
    kind: string;
    title: string;
    org: string;
    period: string;
    coverage: number;
    hits: { key: string; name: string; attain: number; show: () => void }[];
    tasks: string[];
    unmapped: string[];
    show: () => void;
  }

  const shownExp = useMemo<ShownExp[]>(
    () =>
      liveExpLinks.map((l) => ({
        id: l.seg.id,
        kind: l.seg.kind === 'project' ? 'project' : 'work',
        title: l.seg.title,
        org: l.seg.role,
        period: l.seg.period,
        coverage: l.coverage,
        /* 该段支撑到的要求，按逐项判定给出达成程度：
           已满足记满，等级不足与证据不足记半，其余不进入本表 */
        hits: l.hits.map((h) => ({
          key: h.name,
          name: h.name,
          attain: h.gap === 'SATISFIED' ? 1 : 0.5,
          show: () => setLiveFocus(h.name),
        })),
        tasks: l.tasks,
        unmapped: [],
        show: () => showSpans(l.seg.title, [[l.seg.start, l.seg.end]]),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [liveExpLinks],
  );

  /* ---------------- 导出 ----------------
     一份文本报告，逐项列出判定与出处。两条链路的口径一致，故只有一套导出。 */

  const exportReport = () => {
    const t = run.match?.target_job_profile;
    const jd = jdCount ?? targetJob?.jobDef?.n ?? null;
    const out = [
      'JobGraph 人岗匹配诊断报告',
      `目标岗位：${targetJob?.name ?? ''}`,
      jd !== null
        ? `比对基准：${t?.window ?? MATCH_WINDOW} 窗口内该岗位的 ${jd.toLocaleString()} 条招聘信息，逐条统计后按能力归并`
        : '',
      live ? '' : '简历来源：本站内置示例简历，非上传件',
      `岗位要求达成率：${report.score.toFixed(2)}（已验证满足 ${report.counts.satisfied} / 可计分要求 ${report.counts.required_skills}）`,
      `简历一侧：判定为已具备 ${report.summary?.supported ?? 0} 项、证据不足 ${report.summary?.partial ?? 0} 项，行为证据 ${report.evidence.length} 段`,
      run.proficiencyFallback
        ? `注：本次未启用自动熟练度评级（${run.proficiencyNote ?? '服务端未配置模型'}）`
        : '',
      '',
      '逐项差距：',
      ...report.items.map(
        (i) =>
          `- ${i.name}：要求 ${requiredLabel(i.requiredLevel)} / 简历 ${levelLabel(i.candidateLevel)} → ${GAP_TEXT[i.gap]}` +
          (i.evidence.length ? `；证据 ${i.evidence.length} 段` : '；无证据'),
      ),
      ...(liveExcluded.length
        ? ['', `未计入达成率：${liveExcluded.map((e) => `${e.name}（${e.reason}）`).join('、')}`]
        : []),
      '',
      '诊断结论：',
      ...report.advice.map((a) => `- ${a.title}：${a.body}`),
      '',
      '学习路径：',
      ...(report.path?.ready.flatMap((pth) => [
        `- ${pth.name}（${PATH_MODE_TEXT[pth.pathMode]}，目标 ${requiredLabel(pth.requiredLevel)}）`,
        ...pth.steps.map((st, k) => `    ${k + 1}. ${st.nodeName}：${st.evidenceTask}`),
      ]) ?? []),
      '',
      `简历可核验度：${shownAudit.score} / 100（风险 ${shownAudit.risk} 项 · 存疑 ${shownAudit.watch} 项）`,
      ...shownAudit.checks.map((c) => `- [${CHECK_TEXT[c.level]}] ${c.title}：${c.metric}　${c.detail}`),
      '',
      '经历与该岗位能力要求的关联度：',
      ...shownExp.map(
        (l) =>
          `- ${l.title}${l.period ? `（${l.period}）` : ''}覆盖岗位要求权重 ${(l.coverage * 100).toFixed(0)}%；` +
          `支撑到：${l.hits.map((h) => h.name).join('、') || '无'}`,
      ),
      '',
      '该岗位核心任务的覆盖：',
      ...liveTasks
        .slice(0, 8)
        .map(
          (x) =>
            `- ${x.taskName}：${Math.round(x.coverage * 100)}%` +
            (x.weakest.length ? `（薄弱项：${x.weakest.join('、')}）` : ''),
        ),
      '',
      '口径说明：',
      '· 达成率为非对称口径，按该岗位的能力要求逐项判定，简历中超出岗位要求的能力不计分也不扣分。',
      '· 任务覆盖系由上列逐项判定沿图谱的任务—能力权重折算而得；经历关联度按该岗位各项要求的权重计入所在段；' +
      '相近岗位的相似度与能力覆盖取该岗位的能力要求向量。三者用于定位与排序，与达成率不同源、不可换算。',
      '· 真实性核验的七项判据均落在简历全文、上述逐项判定与该岗位招聘原文的锚点句上。',
    ].filter((x) => x !== '');

    const blob = new Blob([out.join('\r\n')], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `JobGraph-${targetJob?.name ?? '目标岗位'}-匹配报告.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    say('报告已导出');
  };

  /* ---------------- 报告分节的次序 ----------------
     实测链路把服务端直接给出结论的三块（诊断结论、能力差距、学习路径）提到前面，
     由判定与图谱合成的几块顺次靠后；演示链路维持原有次序。 */

  /* 报告的分节次序：先结论，再支撑结论的两项核验，然后才是差距、地形与路径。

     两条链路取同一序。此前实测链路把差距明细与学习路径提到核验之前 —— 那时
     核验与经历关联两块取的还是内置示例简历，与上传件无关，排在前面等于让人
     先读一段与自己无关的内容；两块改由实测输入算出之后，这个理由不再成立。 */
  const SEC_ORDER: SecKey[] = ['advice', 'auth', 'exp', 'gap', 'terrain', 'path', 'near'];
  const secPos = (k: SecKey) => ({ order: SEC_ORDER.indexOf(k) });

  /* ---------------- 报告之后的出口 ----------------
     末站的三个跨页出口从页脚那一排按钮里挪出来，与其余三页统一成同一块。
     页脚只留本页自己的动作（重新上传、导出报告）。 */
  const exits = useMemo<StepItem[]>(() => {
    const q = encodeURIComponent(targetId);
    return [
      {
        to: `/jobs?tab=${targetJob?.emerging ? 'new' : 'existing'}&id=${q}`,
        label: `查看“${targetJob?.name ?? '目标岗位'}”的能力变动`,
        desc: '该岗位本期的能力年轮与逐条变更清单，及支撑变动的三类原文。',
        icon: 'trend',
        primary: true,
      },
      {
        to: `/explore?job=${q}`,
        label: '查看能力结构相近的岗位',
        desc: '同一能力构成可覆盖的其他岗位，及其城市与薪资档分布。',
        icon: 'route',
      },
      {
        to: `/panorama?focus=${q}`,
        label: '在全景图谱中定位该岗位',
        desc: '该岗位由哪些核心任务构成，这些任务又要求哪些能力。',
        icon: 'graph',
      },
    ];
  }, [targetId, targetJob]);

  /* ==================== 渲染 ==================== */

  return (
    <>
      <div className="match-wrap">
        <PageGuide station={stationOf('/match')!} landed={fromJobs ? (targetJob?.name ?? null) : null} />

        {/* 取数链路的状态不在页首出现。

            此前服务不可达时页首挂一条横幅。它报的是本机部署状态，不是这一页的
            内容 —— 页面本身是要给外部读者看的，一条"服务不可达"的运行日志摆在
            第一屏，读者既无从处置，也会把它读成系统本身出了问题。
            走的是实测还是演示，仍在上传区那一行与报告内逐块的口径标里写明，
            那两处与它所限定的内容在同一屏。 */}

        {stage !== 'report' && (
          <div className={leaving ? 'mflow leaving' : 'mflow'}>
            {/* ---------------- 三步流程栏 ---------------- */}
            <ol className="msteps" aria-label="匹配流程">
              {STEPS.map((s, i) => {
                const state = i < step ? 'done' : i === step ? 'active' : '';
                // 第二段连线跟着解析进度走，视觉上“一路推到第三步”
                const fill = i === 0 ? (step > 0 ? 100 : 0) : i === 1 ? (stage === 'parsing' ? progress : 0) : 0;
                return (
                  <li key={s.n} className={`mstep ${state}`}>
                    {i < STEPS.length - 1 && (
                      <span className="mstep-line">
                        <i style={{ width: `${fill}%` }} />
                      </span>
                    )}
                    <span className="mstep-dot">{i < step ? <Icon name="check" size={15} /> : s.n}</span>
                    <span className="mstep-copy">
                      <b>{s.title}</b>
                      <small>{s.desc}</small>
                    </span>
                  </li>
                );
              })}
            </ol>

            {stage === 'upload' ? (
              /* ---------------- 上传 ---------------- */
              <div className="mup">
                <section className="mup-main">
                  {/* 上一轮的报告还在时给一个回去的口子：从报告页按"重新上传"过来，
                      看一眼又想回去看结论的人，此前只能重跑一遍解析。

                      此前是标题上方一枚白底描边的小按钮，与页内其余次级按钮同一
                      形貌，读者扫过整屏也未必留意到上一份报告还在。现改为一条
                      带主色的横条，左侧一句话交代它是什么，右侧才是动作。 */}
                  {ui.hasReport && (
                    <button
                      type="button"
                      className="mup-back"
                      onClick={() => {
                        setStage('report');
                        window.scrollTo({ top: 0, behavior: 'auto' });
                      }}
                    >
                      <Icon name="doc" size={15} />
                      <span className="mup-back-t">
                        上一份匹配报告仍在
                        <small>本次不重新解析也可回到该报告</small>
                      </span>
                      <em>返回报告</em>
                    </button>
                  )}
                  <h2>上传简历，识别任务经验与技能画像</h2>
                  <p className="mup-lead">
                    从项目经历中抽取任务与技能要素，计算与目标岗位之间的差距；
                    报告中的每一项均可回溯简历原文核对。
                  </p>

                  <label
                    className={dragging ? 'mdrop drag' : 'mdrop'}
                    onDragEnter={(e) => {
                      e.preventDefault();
                      setDragging(true);
                    }}
                    onDragOver={(e) => e.preventDefault()}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDragging(false);
                      const f = e.dataTransfer.files?.[0];
                      if (f) {
                        setFileName(f.name);
                        setFile(f);
                      }
                    }}
                  >
                    <span className="mdrop-ic">
                      <Icon name="doc" size={28} />
                    </span>
                    <b>将简历拖拽至此处</b>
                    <small>或点击选择文件 · 支持 .pdf / .doc / .docx，建议不超过 10 MB</small>
                    <span className="mdrop-btn">选择简历文件</span>
                    <small className="mdrop-privacy">
                      <Icon name="shield" size={13} />
                      {backendReady
                        ? '文件送至本机运行的解析服务，不经第三方留存'
                        : '文件仅在本地解析，不会上传'}
                    </small>
                    <input
                      type="file"
                      accept={backendReady ? '.pdf,.docx,.txt' : '.pdf,.doc,.docx,.txt,.md'}
                      onChange={(e) => {
                        const f = e.target.files?.[0] ?? null;
                        setFileName(f?.name ?? null);
                        setFile(f);
                      }}
                    />
                  </label>

                  {file && fileName && (
                    <div className="mfile">
                      <span className="mfile-ic">
                        <Icon name="doc" size={17} />
                      </span>
                      <span className="mfile-text">
                        <b>{fileName}</b>
                        <small>
                          {backendReady
                            ? '格式受支持，将由解析服务处理'
                            : '格式受支持；解析服务未接入，本次在本地解析'}
                        </small>
                      </span>
                      <button
                        className="btn sm"
                        onClick={() => {
                          setFileName(null);
                          setFile(null);
                        }}
                      >
                        移除
                      </button>
                    </div>
                  )}

                  {/* 示例简历一行排完：提示语与三枚选项同处一行，选项不再另起一行。
                      下面那行简历概况只在选中之后才有内容，但它的高度始终占着 ——
                      按下一枚选项就把下方的开始解析整条推下去，读者会以为点错了。 */}
                  <div className="msample">
                    <div className="msample-row">
                      <span className="msample-hd">或载入一份脱敏示例简历：</span>
                      {d.resumes.map((r, i) => (
                        <button
                          key={r.name}
                          className={i === resumeIdx ? 'chip on' : 'chip'}
                          onClick={() => {
                            /* 再点一次即取消选择 —— 已选件那张卡片不再为示例简历
                               出现，取消的入口因而落回选项本身 */
                            const off = i === resumeIdx;
                            setResumeIdx(off ? -1 : i);
                            setFileName(off ? null : r.name.replace(/^示例简历 · /, ''));
                            /* 示例简历是本站内置的结构化数据，没有可上传的文件实体 */
                            setFile(null);
                          }}
                        >
                          {r.name.replace(/^示例简历 · /, '')}
                        </button>
                      ))}
                    </div>
                    <p className="msample-note" aria-hidden={resumeIdx < 0}>
                      {resumeIdx >= 0 && (
                        <>
                          共 {resume.skillPoints.length} 个技能点 · {resume.experiences.length} 段经历 ·{' '}
                          {resume.years} 年经验 · {resume.degree} · {resume.city}
                        </>
                      )}
                    </p>
                  </div>

                  <div className="mup-act">
                    <span className="mup-act-hint">
                      当前意向岗位：<b>{targetJob?.name}</b>
                      {fromJobs && <em>（来自岗位洞察）</em>}
                      {canRunLive && <em>· 基准 JD 已选定</em>}
                    </span>
                    <div className="mup-btns">
                      {/* 此处原挂一枚"直接查看演示报告"。载入示例简历再走一遍解析，
                          看到的就是同一份演示报告，且沿途各步与真实上传件完全一致；
                          另设一个跳过解析的入口，等于给同一件事开两条路。 */}
                      <button
                        className="btn primary"
                        disabled={!fileName}
                        onClick={() => startRun(canRunLive ? 'live' : 'demo')}
                      >
                        开始解析
                        <Icon name="arrowR" size={15} />
                      </button>
                    </div>
                  </div>

                  {/* 此处原挂一条解析服务的可达性提示（未配置地址 / 正在探测 / 不可达）。
                      服务的部署状态是本站自己的事，不该出现在页面上：读者要判断的是
                      这份报告算得对不对，而不是某个进程通没通。本次是否走了实测链路，
                      报告内逐块另有标注，那一处说的是数据的来源，与此不同。 */}
                  {file && backendReady && !targetInWindow && (
                    <p className="mup-warn">
                      “{targetJob?.name}”在 {MATCH_WINDOW} 窗口内没有招聘信息样本，
                      无从得出该岗位的能力要求；请改选其他岗位。
                    </p>
                  )}
                </section>

                <aside className="mtarget">
                  <h3>
                    选择意向岗位
                    {/* 四项属性现已全部有实测来源，故不再挂演示数据标；
                        学历一项由招聘正文的门槛语抽出，属推导，口径写进问号 */}
                  </h3>
                  <p>
                    报告以该岗位的核心任务与能力要求为基准计算；进入报告后可随时切换岗位并重新计算。
                    尚未确定意向岗位时，可先在
                    <button className="mlink" onClick={() => nav('/explore')}>
                      职业探索
                    </button>
                    中从能力出发筛选。
                  </p>
                  <div className="mtarget-search">
                    <Icon name="search" size={15} />
                    <input
                      type="text"
                      value={query}
                      placeholder="搜索岗位名称或方向…"
                      aria-label="搜索意向岗位"
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </div>
                  <div className="mtarget-list">
                    {filteredJobs.map((j) => (
                      <button
                        key={j.id}
                        className={j.id === targetId ? 'mtg on' : 'mtg'}
                        onClick={() => setTargetId(j.id)}
                      >
                        <i className="mtg-radio" />
                        <span className="mtg-text">
                          <b>
                            {j.name}
                            {j.emerging && <em className="mtg-new">萌芽</em>}
                          </b>
                          {/* 这三项都取自招聘信息，尚未进入招聘市场的萌芽岗位一项也填不出：
                              照原样铺出来是"（空）· — · 在招 0"，右侧再跟一个 0k，
                              连起来读成"在招 00k"。这一行改写它自己有的那两项。 */}
                          <small>
                            {j.emerging
                              ? `尚未归入体系 · 前瞻强度 ${(j.gap * 100).toFixed(0)}%`
                              : `${j.cluster} · ${topKey(j.attrs?.degrees)} · 在招 ${j.attrs?.postCount?.toLocaleString()}`}
                          </small>
                        </span>
                        <span className="mtg-pay">{j.emerging ? '' : `${j.attrs?.medianSalary}k`}</span>
                      </button>
                    ))}
                    {filteredJobs.length === 0 && <p className="mtarget-empty">未找到匹配的岗位，请更换关键词。</p>}
                  </div>

                  {/* 此处原有一枚"比对基准"下拉，要求先在该岗位的招聘信息里挑一条，
                      报告以那一条逐项比对。求职者要判断的是与这个岗位的差距，
                      不是与某一家公司某一条启事的差距，而"挑哪一条"既无从判断，
                      挑错了结论还会跟着变。基准改由该岗位窗口内的全部招聘信息
                      汇总而来（见后端 aggregated_target_job_service），选岗位即可开始。 */}
                </aside>
              </div>
            ) : (
              /* ---------------- 解析 ----------------

                 一份竖排的工序单，不再是"大圆球 + 一条总进度 + 四张并排瓦片"。
                 改竖排有两个理由：四道工序耗时相差两个数量级（抽取一步以分钟计，
                 其余三步以毫秒到秒计），横排等宽会让人以为四步各占四分之一；
                 竖排给每一步一行，正在跑的那一行自己带一条进度，跑到哪一步、
                 那一步跑到什么程度，两件事各归各的位置。

                 页面自身不再解释"为什么慢""为什么只认行为证据"—— 前者由正在跑的
                 那一行的用时说明，后者是报告的口径，报告里已逐处交代。 */
              <section className="mparse">
                <header className="mparse-hd">
                  <div className="mparse-hd-t">
                    <h2>正在解析简历</h2>
                    <p>
                      {fileName ?? '已上传简历'}
                      {runMode === 'live' && targetJob && (
                        <>
                          <i />
                          目标岗位 {targetJob.name}
                        </>
                      )}
                    </p>
                  </div>
                  <div className={`mparse-pct${liveFailIdx >= 0 ? ' failed' : ''}`}>
                    <b>{Math.round(progress)}</b>
                    <em>%</em>
                  </div>
                </header>

                <div className={`mparse-track${liveFailIdx >= 0 ? ' failed' : ''}`}>
                  <i style={{ width: `${progress}%` }} />
                </div>

                <ol className="mparse-pipe">
                  {(runMode === 'live' ? LIVE_PIPELINE : PIPELINE).map((p, i) => {
                    const failed = i === liveFailIdx;
                    /* 实测链路一律按步骤判定，与 active 同源，中间不会留下判不出完成的空格；
                       出错时失败格之前的算已完成，之后的一律未开始。演示链路无步骤，仍按进度判 */
                    const done =
                      runMode === 'live'
                        ? i < (liveFailIdx < 0 ? liveDoneCount : liveFailIdx)
                        : progress >= p.until;
                    const active =
                      liveFailIdx < 0 &&
                      !done &&
                      i === (runMode === 'live' ? livePipeIdx : stageIdx);
                    /* 本步内部的进度：总进度落在本步区间里的位置。
                       区间以本步上限与上一步上限为界，故最后一步跑满时正好到头。 */
                    const pipe = runMode === 'live' ? LIVE_PIPELINE : PIPELINE;
                    const from = i > 0 ? pipe[i - 1].until : 0;
                    const inner = Math.max(0, Math.min(1, (progress - from) / Math.max(1, p.until - from)));
                    return (
                      <li
                        key={p.title}
                        className={failed ? 'failed' : done ? 'done' : active ? 'active' : ''}
                      >
                        <span className="mpp-ic">
                          <Icon name={failed ? 'close' : done ? 'check' : p.icon} size={15} />
                        </span>
                        <span className="mpp-t">
                          <b>{p.title}</b>
                          {active && <small>{liveNote}</small>}
                        </span>
                        <span className="mpp-st">
                          {failed ? '未能完成' : done ? '已完成' : active ? '进行中' : '等待'}
                        </span>
                        {active && (
                          <span className="mpp-bar">
                            <i style={{ width: `${(inner * 100).toFixed(1)}%` }} />
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </section>
            )}
          </div>
        )}

        {/* ---------------- 报告：左简历 · 右分析 ---------------- */}
        {stage === 'report' && (
          <div className="mreport">
            {/* ==================== 左：简历一侧 ====================

                原文与右栏的结论双向联动：右栏点一项能力，左栏就地标出它的落点。

                "返回上传"落在本栏的页眉里，与这一栏的主语（本次读的是哪一份简历）
                同处一行。它此前独占报告顶端的一整行，一行只放一枚小按钮，
                既占着首屏最先被读到的位置，又因四周无物而看不出与谁相关。 */}
            <LiveResumeDoc
              fileName={live ? fileName : `${resume.name.replace(/^示例简历 · /, '')} · 内置示例`}
              summary={report.summary}
              resumeText={report.resumeText}
              evidence={report.evidence}
              mentions={report.mentions}
              focusSkill={liveFocus}
              focusSpans={liveSpans}
              onClearFocus={clearFocus}
              onBack={() => {
                setStage('upload');
                setProgress(0);
                window.scrollTo({ top: 0, behavior: 'auto' });
              }}
              picker={
                live ? null : (
                  <select
                    aria-label="切换示例简历"
                    value={Math.max(0, resumeIdx)}
                    onChange={(e) => {
                      setResumeIdx(Number(e.target.value));
                      clearFocus();
                    }}
                  >
                    {d.resumes.map((r, i) => (
                      <option key={r.name} value={i}>
                        {r.name.replace(/^示例简历 · /, '')}
                      </option>
                    ))}
                  </select>
                )
              }
            />

            {/* ==================== 右：分析结果 ==================== */}
            <article className="rp rp-live">
              {/* 页眉：目标岗位 + 综合匹配度 + 多维度 */}
              <header className="rp-hd">
                <div className="rp-hd-l">
                  <h1>{targetJob?.name}</h1>
                  <div className="rp-tags">
                    {/* 多数岗位的聚类名与类别名相同（如“软件开发”），两枚并排读起来像两项属性 */}
                    <span className="tag-p">{targetJob?.cluster}</span>
                    {targetJob?.category !== targetJob?.cluster && (
                      <span className="tag-o">{targetJob?.category}</span>
                    )}
                    <span className="tag-o">{topKey(targetJob?.attrs?.experience)}</span>
                    <span className="tag-o">中位 {targetJob?.attrs?.medianSalary}k</span>
                    {targetJob?.emerging && <span className="tag-n">萌芽岗位</span>}
                  </div>

                  {/* 目标岗位。改选之后整份报告随之重算。
                      此处原先并排还有一枚"基准招聘信息"下拉，要求先在该岗位的数百条
                      招聘信息里挑一条，报告以那一条逐项比对。求职者要判断的是与这个
                      岗位的差距，不是与某一家公司某一条启事的差距；挑哪一条既无从
                      判断，挑错了结论还会跟着变。现由该岗位窗口内的全部招聘信息
                      汇总出一份基准，选岗位即可，见后端 aggregated_target_job_service。 */}
                  <div className="rp-base">
                    <label className="rp-base-lb" htmlFor="rp-target">
                      目标岗位
                    </label>
                    <div className="rp-base-sel">
                      <select
                        id="rp-target"
                        value={targetId}
                        disabled={live && run.phase === 'matching'}
                        onChange={(e) => {
                          setTargetId(e.target.value);
                          clearFocus();
                          setLiveFocus(null);
                          say(`已切换至“${jobById.get(e.target.value)?.name}”并重新计算`);
                        }}
                      >
                        {/* 岗位类别到数百量级时，一条按匹配度排下来的长列表翻不动。
                            先给一组匹配度最高的，其余按岗位聚类分组，组内仍按匹配度排序。 */}
                        <optgroup label="匹配度最高">
                          {targetOptions.top.map((j) => (
                            <option key={j.id} value={j.id}>
                              {j.name} · 覆盖 {(scoreOf(j.id) * 100).toFixed(1)}%
                            </option>
                          ))}
                        </optgroup>
                        {targetOptions.groups.map((g) => (
                          <optgroup key={g.cluster} label={`${g.cluster}（${g.items.length}）`}>
                            {g.items.map((j) => (
                              <option key={j.id} value={j.id}>
                                {j.name} · 覆盖 {(scoreOf(j.id) * 100).toFixed(1)}%
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      <Icon name="chevronD" size={16} />
                    </div>

                    {/* 基准取自多少条招聘信息，是这份报告可信到什么程度的第一道依据，
                        故与选择器同列。窗口与岗位编码一类的内部标识不上界面。

                        改选岗位后重算走的是纯规则比对（熟练度档位已在本轮第一次
                        比对时一次算齐），实测一秒以内，故不另开解析屏；这一行
                        在重算途中改为进行时，页面其余各块留在原处不闪。 */}
                    {live && run.phase === 'matching' ? (
                      <p className="rp-base-meta busy">
                        <i className="rp-base-spin" aria-hidden="true" />
                        正在按该岗位的能力要求重新比对…
                      </p>
                    ) : (
                      baseCount !== null && (
                        <p className="rp-base-meta">
                          基准为该岗位 {MATCH_WINDOW} 窗口内的 <b>{baseCount.toLocaleString()}</b> 条招聘信息，
                          逐条统计后按能力归并
                        </p>
                      )
                    )}
                  </div>
                </div>

                {/* 达成率与其四态构成。两者同源，不再另给评级档位 ——
                    判定阈值尚未标定，任何档位都会是无据的加工。 */}
                <div className="rp-hd-r">
                  <div className="rp-ring">
                    <ScoreRing value={report.score / 100} />
                    <div className="rp-ring-val">
                      <b>{report.score.toFixed(1)}</b>
                      <small>岗位要求达成率</small>
                    </div>
                  </div>
                  {/* 达成率的口径与阈值状态两段说明，此前各占三行压在读数下面，
                      两段都是一次读懂之后不必再读的话，故一并收进问号。 */}
                  <p className="rp-verdict">
                    <b>
                      {report.counts.satisfied} / {report.counts.required_skills} 项已满足
                    </b>
                  </p>

                  <div className="rp-dims-hd">
                    达成率的构成 · 共 {report.counts.required_skills} 项可计分要求
                  </div>
                  <LiveScoreBar summary={report.counts} />

                  {run.proficiencyFallback && (
                    <p className="rp-nocal warn">
                      本次未启用自动熟练度评级（{run.proficiencyNote ?? '服务端未配置模型'}）。
                      已具备但无法定级的能力将计入“证据不足”，达成率因而偏低。
                    </p>
                  )}

                  {run.lowQualityAccepted && (
                    <p className="rp-nocal warn">
                      本次简历的版面还原未达质量门槛，系确认后继续解析。
                      抽出的文字可能与原文错位，下列各项结论须逐条回原文核对。
                    </p>
                  )}
                </div>
              </header>

              {/* 结论先行：针对性改进建议 */}
              <section className="rp-sec" style={secPos('advice')}>
                <SecHead title="诊断结论与改进建议" />
                <div className="rp-advice">
                  {report.advice.map((a) => (
                    <div key={a.title} className={`adv adv-${a.kind}`}>
                      <b>{a.title}</b>
                      <p>{a.body}</p>
                    </div>
                  ))}
                </div>
              </section>

              {/* 真实性核验。实测链路下七项判据全部落在实测输入上：解析全文、
                  服务端逐项判定与证据偏移、技能清单列名、该岗位招聘原文的锚点句。 */}
              <section className="rp-sec" style={secPos('auth')}>
                <SecHead title="简历真实性与一致性核验" />

                <div className="auth-top">
                  <div className="auth-score">
                    <b>{shownAudit.score}</b>
                    <small>可核验度 / 100</small>
                  </div>
                  <div className="auth-sum">
                    <p>
                      共 {shownAudit.checks.length} 项核验：
                      <span className="ck ck-pass">
                        通过 {shownAudit.checks.filter((c) => c.level === 'pass').length}
                      </span>
                      <span className="ck ck-watch">存疑 {shownAudit.watch}</span>
                      <span className="ck ck-risk">风险 {shownAudit.risk}</span>
                    </p>
                  </div>
                </div>

                <div className="auth-list">
                  {shownAudit.checks.map((c) => (
                    <article key={c.id} className={`ac ac-${c.level}`}>
                      <div className="ac-hd">
                        <span className={`ac-flag ac-${c.level}`}>{CHECK_TEXT[c.level]}</span>
                        <b>{c.title}</b>
                        <span className="ac-metric">{c.metric}</span>
                        {c.origin > 0 && (
                          <button className="ac-origin" onClick={() => c.show()}>
                            <Icon name="doc" size={12} />
                            看原文（{c.origin}）
                          </button>
                        )}
                      </div>
                      <p>{c.detail}</p>
                      {c.items && c.items.length > 0 && (
                        <div className="ac-items">
                          {c.items.map((it) => (
                            <span key={it}>{it}</span>
                          ))}
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              </section>

              {/* 经历关联度。服务端对上传件走整篇抽取，不返回经历元信息，但每条证据
                  都带着落在全文上的字符偏移；据此按简历版面切段并把证据归入所在段，
                  切分规则与服务端切内部记录时的日期行规则一致。 */}
              <section className="rp-sec" style={secPos('exp')}>
                <SecHead title="经历与岗位能力的关联度" />
                {shownExp.length === 0 && (
                  <p className="rp-note">
                    未能从简历版面中切出可分辨的经历段（多见于起止时间未单独成行的排版）。
                    左栏已把全部行为证据就地标注在原文上，可据以核对每段经历各支撑了哪些能力。
                  </p>
                )}
                <div className="xl-list">
                  {shownExp.map((l) => (
                    <article key={l.id} className="xl">
                      <div className="xl-hd">
                        <span className={`xl-kind k-${l.kind}`}>{EXP_KIND[l.kind]}</span>
                        <b>{l.title}</b>
                        <span className="xl-org">{[l.org, l.period].filter(Boolean).join(' · ')}</span>
                        <button className="ac-origin" onClick={() => l.show()}>
                          <Icon name="doc" size={12} />
                          看原文
                        </button>
                      </div>

                      <div className="xl-cov">
                        <span className="xl-cov-bar">
                          <i style={{ width: `${Math.min(100, l.coverage * 100)}%` }} />
                        </span>
                        <b>{(l.coverage * 100).toFixed(0)}%</b>
                        <small>覆盖该岗位的能力要求权重</small>
                      </div>

                      {l.hits.length > 0 ? (
                        <>
                          <div className="xl-hits">
                            {l.hits.map((h) => (
                              <button key={h.key} className="xl-hit" onClick={() => h.show()}>
                                <span className="xl-hit-n">{h.name}</span>
                                <span className="xl-hit-bar">
                                  <i
                                    className={h.attain >= 0.85 ? 'ok' : h.attain >= 0.5 ? 'mid' : 'low'}
                                    style={{ width: `${h.attain * 100}%` }}
                                  />
                                </span>
                                <span className="xl-hit-v">{Math.round(h.attain * 100)}%</span>
                              </button>
                            ))}
                          </div>
                          {l.tasks.length > 0 && (
                            <p className="xl-tasks">
                              对应岗位的核心任务：{l.tasks.map((t) => `“${t}”`).join('、')}
                            </p>
                          )}
                        </>
                      ) : (
                        <p className="xl-empty">
                          该段经历的技术栈与本岗位的能力要求基本不重叠，未构成有效支撑。
                        </p>
                      )}

                      {l.unmapped.length > 0 && (
                        <p className="xl-unmapped">
                          未对齐至图谱的自述项：{l.unmapped.join('、')}（表述差异或尚未纳入技能点体系）。
                        </p>
                      )}
                    </article>
                  ))}
                </div>
              </section>

              {/* 能力差距：整节取自逐项判定 */}
              <section className="rp-sec" id="rp-items" style={secPos('gap')}>
                <SecHead title="能力差距明细" />
                <LiveGapLedger items={report.items} onPickEvidence={(it) => setLiveFocus(it.name)} />

                {/* 此处原有一块"岗位核心任务的覆盖情况"，把逐项能力判定沿图谱的
                    任务—能力权重折算到该岗位的每项核心任务上。折算这一步要先
                    交代一句口径，八项任务的读数又落在同一档（三成八到四成九），
                    任务之间分不开，读者只能得到"哪一项都差不多"这一个印象。
                    差距本就已在上表逐项列明，此处不再换一个坐标重排一遍。 */}
              </section>

              {/* 地形：岗位之间的方位取自图谱的能力结构；简历的落点按服务端判定得到的
                  能力持有度对各岗位的要求权重加权算出，两侧均为实测。 */}
              <section className="rp-sec" style={secPos('terrain')}>
                <SecHead title="能力地形导航" />
                <CareerTerrain
                  jobs={jobs}
                  coords={coords}
                  resumePos={resumePos}
                  targetJobId={targetId}
                  waypoints={waypoints}
                  onPickJob={(id) => {
                    setTargetId(id);
                    clearFocus();
                    say(`已切换至“${jobById.get(id)?.name}”并重新计算`);
                  }}
                />
              </section>

              {/* 学习路径。实测链路取服务端的冻结规划器，内置链路按同一批
                  能力发展图谱在前端组装，见 data/demoLive.ts。 */}
              <section className="rp-sec" style={secPos('path')}>
                <SecHead title="学习路径规划" />
                {report.path && <LivePathPlan path={report.path} />}
              </section>

              {/* 相近岗位：岗位之间的相似度为实测，本简历对它们的匹配度不是 */}
              <section className="rp-sec" style={secPos('near')}>
                <SecHead title="相近岗位" />
                <ul className="rp-near">
                  {near.map((n) => {
                    const j = jobById.get(n.jobId);
                    if (!j) return null;
                    const mine = scoreOf(n.jobId);
                    return (
                      <li key={n.jobId}>
                        <span className="nr-n">
                          <b>{j.name}</b>
                          <small>
                            {j.emerging
                              ? `尚未归入体系 · 前瞻强度 ${(j.gap * 100).toFixed(0)}%`
                              : `${j.cluster} · 在招 ${j.attrs?.postCount?.toLocaleString()} · 中位 ${j.attrs?.medianSalary}k`}
                          </small>
                        </span>
                        <span className="nr-m">
                          <small>与目标岗位相似</small>
                          <b>{(n.sim * 100).toFixed(0)}%</b>
                        </span>
                        <span className="nr-m">
                          <small>本简历能力覆盖</small>
                          <b>{(mine * 100).toFixed(1)}%</b>
                        </span>
                        <button
                          className="btn sm"
                          onClick={() => {
                            setTargetId(n.jobId);
                            clearFocus();
                          }}
                        >
                          切换至该岗位
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>

              {/* 页脚：两个出口。
                  此处原先另有一行版本号与评测说明（能力画像 r4.3.4 · 岗位窗口 ·
                  能力体系版本…）。那是核对用的内部标识，读者据以行动的信息一件
                  也不在其中，而它占着整页最后一屏、还带一层底色，读起来像是结论。 */}
              <footer className="rp-ft">
                <div className="rp-ft-act">
                  <button
                    className="btn"
                    onClick={() => {
                      /* 只切回上传屏，不清这一轮的结果：清掉之后上传屏那枚
                         "返回报告"就没有可回的东西了。真正的清空发生在选定
                         新的简历、按下开始解析的那一刻（见 startRun）。 */
                      setStage('upload');
                      setProgress(0);
                      clearFocus();
                      setLiveFocus(null);
                      window.scrollTo({ top: 0, behavior: 'auto' });
                    }}
                  >
                    <Icon name="refresh" size={14} />
                    重新上传简历
                  </button>
                  <button className="btn primary" onClick={exportReport}>
                    <Icon name="doc" size={14} />
                    导出报告
                  </button>
                </div>
              </footer>
            </article>
          </div>
        )}

        {/* 页尾出口挂在两列网格之外 —— 它是整页的出口，不属于“左简历 · 右分析”
            这一格布局。放在网格内会被自动排进第二行左列，只占左列宽度，
            且常驻的简历卡片会滑到网格末端与它叠在一起。 */}
        {stage === 'report' && <NextSteps from="/match" items={exits} />}
      </div>

      {/* ---------------- 解析中断 ----------------

          原先这两件事各占解析页正文的一大块，把工序单挤到上半屏，且都在正文里
          铺开解析器名、字符数、页数与旗标清单 —— 那是排障用的字段，不是使用者
          要读的东西。两者现改为弹窗：出错就一句话说清哪一步没跑完、下一步怎么办，
          技术细节收进折叠项，要排障时再展开。 */}
      {runMode === 'live' && stage === 'parsing' && run.phase === 'error' && (
        <MatchDialog
          title="解析未能完成"
          lead={`${LIVE_PIPELINE[liveFailIdx]?.title ?? '解析'}这一步没有跑完，本次未能生成报告。`}
          detail={run.error}
          onClose={() => setStage('upload')}
          actions={
            <>
              <button className="btn" onClick={() => setStage('upload')}>
                返回重试
              </button>
              <button
                className="btn primary"
                onClick={() => {
                  setRunMode('demo');
                  setStage('report');
                }}
              >
                改用内置示例简历
              </button>
            </>
          }
        />
      )}

      {/* 预检不过关：此步尚未调用模型，停下来把问题说清，由使用者决定去留 */}
      {runMode === 'live' && stage === 'parsing' && run.phase === 'quality_hold' && run.preflight && (
        <MatchDialog
          title="简历版面未能完整还原"
          lead={
            '从这份文件里取到的文字与原文对不上位，多半是扫描件、图片版或两栏排版。' +
            '继续解析会得到一份看似有据、实则指不回原文的报告。建议改用文字版 PDF、DOCX 或纯文本。'
          }
          items={run.preflight.quality.flags.map((f) => QUALITY_FLAG_TEXT[f] ?? f)}
          detail={
            `解析器 ${run.preflight.parser}；取到 ${run.preflight.quality.char_count} 字、` +
            `${run.preflight.quality.nonempty_line_count} 个非空行` +
            (run.preflight.quality.page_count > 0
              ? `；${run.preflight.quality.page_count} 页中有 ${run.preflight.quality.empty_page_count} 页取不到文字`
              : '')
          }
          onClose={() => {
            setStage('upload');
            setProgress(0);
            resetLive();
          }}
          actions={
            <>
              <button
                className="btn"
                onClick={() => {
                  if (file && targetCode) void proceedAnyway(file, targetCode);
                }}
              >
                仍然继续
              </button>
              <button
                className="btn primary"
                onClick={() => {
                  setStage('upload');
                  setProgress(0);
                  resetLive();
                }}
              >
                换一份文件
              </button>
            </>
          }
        />
      )}

      {toast && <div className="mtoast">{toast}</div>}
      <Footer />
    </>
  );
}

/* ==================== 小构件 ==================== */

/**
 * 解析中断时的弹窗。
 *
 * 只出现在解析这一步，且一次只有一个，故不另设通用弹窗层。正文一句话说清
 * 发生了什么与下一步怎么办；接口返回的原文与解析器字段收在折叠项里 ——
 * 它们对使用者没有意义，但排障时缺了就无从判读故障位置。
 */
function MatchDialog({
  title,
  lead,
  items,
  detail,
  actions,
  onClose,
}: {
  title: string;
  lead: string;
  items?: string[];
  detail?: string | null;
  actions: React.ReactNode;
  onClose: () => void;
}) {
  /* Esc 与点遮罩都算关闭，与站内既有弹窗一致 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="mdlg-mask" onClick={onClose}>
      <div
        className="mdlg"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="mdlg-t"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mdlg-hd">
          <span className="mdlg-ic">
            <Icon name="alert" size={18} />
          </span>
          <h2 id="mdlg-t">{title}</h2>
          <button className="mdlg-x" onClick={onClose} aria-label="关闭">
            <Icon name="close" size={16} />
          </button>
        </header>
        <p className="mdlg-lead">{lead}</p>
        {items && items.length > 0 && (
          <ul className="mdlg-items">
            {items.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        )}
        {detail && (
          <details className="mdlg-detail">
            <summary>技术细节</summary>
            <p>{detail}</p>
          </details>
        )}
        <footer className="mdlg-ft">{actions}</footer>
      </div>
    </div>
  );
}

/**
 * 简历原文里把抽取到的技能点标黄。
 * 按词长从长到短匹配，避免“Transformer架构”被“Transformers”先切走一半。
 */

/* 分段标题。只留记号与标题：每块标题下面压两三行说明，
   一屏之内就有七八段这样的话，真正要看的数字全被推到下面去了。

   记号取全站一级分区标题通用的那一枚菱形（见 global.css“标题记号”）。此前这一位
   放的是 01–07 的序号，理由是报告七节有先后、序号比菱形多带一层次序；但首页、
   全景图谱、岗位洞察各页的同级标题一律用菱形，只此一页另用一形，读起来像另立了
   一套层级。次序由分节自身的先后表达，不必再靠记号说第二遍。 */
function SecHead({ title }: { title: string }) {
  return (
    <div className="rp-sec-hd">
      <h2>{title}</h2>
    </div>
  );
}

function ScoreRing({ value, size = 138, stroke = 12 }: { value: number; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id="rp-ring-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--primary)" />
          <stop offset="1" stopColor="var(--cyan)" />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={stroke} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="url(#rp-ring-g)"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${(c * Math.max(0, Math.min(1, value))).toFixed(2)} ${c.toFixed(2)}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}
