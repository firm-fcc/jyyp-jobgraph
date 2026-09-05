/* ============================================================
   人岗匹配后端（backend/）的取数层

   这是全站第一处需要在线服务的取数：其余各页读的是 data-pipeline 生成的
   静态 JSON，本页读的是一个本地运行的 FastAPI。两者口径不同，也各自独立 ——
   后端不可达时本页整体回落演示链路，不影响其他页。

   接口契约见 backend/frontend/FRONTEND_API_REFERENCE.md，本文件只做搬运与类型标注，
   不做任何加工：加工在 data/matchLive.ts。两者分开，是为了让“哪些数是后端给的”
   这个问题有一处确定的出处 —— 凡在本文件出现的字段一律来自后端，凡不在的一律不是。

   VITE_MATCH_API 未配置时 LIVE_MATCH 为 false，页面走原有的演示链路。
   ============================================================ */

/* ---------------- 契约类型 ---------------- */

export type ProficiencyLevel = 'P1' | 'P2' | 'P3' | 'P4' | 'U';
export type GapType = 'SATISFIED' | 'LEVEL_GAP' | 'EVIDENCE_INSUFFICIENT' | 'MISSING';
export type MatchDecision = 'MATCH' | 'NO_MATCH' | 'NOT_CALIBRATED';
export type PathMode = 'LEARN' | 'DEEPEN' | 'VERIFY_FIRST' | 'NONE';

/** 一条落在简历原文上的证据。start/end 为字符偏移，解析失败时为 null */
export interface CandidateEvidence {
  text: string;
  source_experience_id: string;
  start: number | null;
  end: number | null;
  fact?: string;
  behavior?: string;
  context?: string;
  result?: string;
}

export interface CandidateSkillAssessment {
  candidate_id: string;
  team_skill_id: string;
  team_skill_name: string;
  status: 'supported' | 'partially_supported' | 'unsupported';
  inference_mode: 'direct_behavior' | 'aggregate_signal';
  evidence: CandidateEvidence[];
  reason: string;
  confidence: number | null;
  atomic_abilities: string[];
  audit_flags: string[];
}

export interface CandidateSkillProfile {
  candidate_id: string;
  skill_registry_version: string;
  assessments: CandidateSkillAssessment[];
  metadata: Record<string, unknown>;
}

/** 简历技能清单里列到的一项。只作展示，不构成对能力的支撑 */
export interface ExplicitSkillMention {
  text: string;
  start: number;
  end: number;
  source: string;
  mention_type: string;
  mapping_status: string;
}

/** POST /api/candidate 的返回 */
/** 原文里已定位的细粒度线索。hint_authority 为非权威标注，不升级能力支持状态 */
export interface GroundedCapabilityCandidate {
  text: string;
  start: number | null;
  end: number | null;
  source_experience_id?: string | null;
  hint_authority?: string;
  [k: string]: unknown;
}

/** 解析器切出的一段经历。切不可靠时整表为空，见 experience_metadata_available */
export interface CandidateSourceSegment {
  source_experience_id: string;
  section_type: string;
  start: number | null;
  end: number | null;
  text: string;
}

export interface CandidateApiResponse {
  /** 2026-09-03 交付起为 v1_1；旧版为 v1，两版的差别只在下面三个新增字段 */
  schema_version: 'candidate_api_response_v1_1' | 'candidate_api_response_v1';
  candidate_id: string;
  candidate_skill_profile: CandidateSkillProfile;
  explicit_skill_mentions: ExplicitSkillMention[];
  /**
   * 解析后的简历全文。证据与列名的 start/end 均为该文本上的字符偏移，
   * 报告左栏据此把证据就地标注回原文。解析器取不到文字时为空串，
   * 旧版服务不返回此字段，两种情形左栏一律退回按经历分组的证据清单。
   */
  resume_text?: string;
  diagnostics: Record<string, unknown>;
  /** v1_1 新增：原文已定位的诊断线索，不是正式能力标签，仅作提示 */
  grounded_capability_candidates?: GroundedCapabilityCandidate[];
  /** v1_1 新增：经历分段。为空且下一项为 false 时，不得声称已可靠切出经历 */
  source_segments?: CandidateSourceSegment[];
  experience_metadata_available?: boolean;
  runtime_schema?: string | null;
  proficiency_status?: string;
}

/** POST /api/candidate?preflight=true 的返回，不调用 LLM，用于先看解析质量 */
export interface CandidatePreflight {
  schema_version: 'candidate_preflight_v1';
  parser: string;
  quality: {
    passed: boolean;
    fallback_required: boolean;
    flags: string[];
    char_count: number;
    nonempty_line_count: number;
    readable_char_ratio: number;
    page_count: number;
    empty_page_count: number;
    empty_page_ratio: number;
  };
  team_skill_registry_version: string;
  team_skill_count: number;
}

/** GET /api/jobs 的一条 JD 摘要 */
export interface JdItem {
  jd_key: string;
  jobid: string;
  title: string;
  std_job: string;
  level: string;
  techstack: string;
  opentime: string;
  n_skills: number;
  n_prof: number;
  salary: string;
  work_year: string;
}

export interface JobCatalogResponse {
  schema_version: 'job_catalog_response_v1';
  query: string;
  std_job: string;
  limit: number;
  total: number;
  items: JdItem[];
}

/** GET /api/job-index：每个标准岗位名下的 JD 条数 */
export interface JobIndexResponse {
  schema_version: 'job_index_v1';
  window: string;
  total_jd: number;
  counts: Record<string, number>;
}

/* ---------------- GET /api/job-summary/{job_code} ----------------

   /api/target-job 给的是单条招聘信息的要求，达成率即按它逐项判定；本接口给的
   是同一岗位在整个窗口内全部招聘信息的汇总 —— 算法工程师一岗即 234 条。
   每项能力带该窗口内提到它的条数与占比，是这批数据里岗位能力要求最直接的读数。

   报告页有三处要的正是这个「该岗位到底要什么、各要到什么份上」：岗位选择器的
   覆盖度排序、能力地形的定位、相近岗位的相似度与覆盖。此前它们取图谱的岗位—
   能力边，而那份权重叠加了论文与新闻的增量修正，各岗位之间被拉平（全站两两
   相似度中位数 0.99）；本接口的提及率区间完整（0.855 至 0），可用于排序。 */

/** 该岗位在本窗口内对某一项能力的要求强度 */
export interface JobSummarySkill {
  team_skill_id: string;
  team_skill_name: string;
  skill_type: 'hard' | 'soft';
  is_primary: boolean;
  /** 窗口内提到这一项的招聘信息条数 */
  jd_presence_count: number;
  /** 上一项占该岗位全部招聘信息的比例 0–1 */
  jd_presence_rate: number;
  /** 提到这一项时所要求的熟练度分布，未写明等级的计入 U */
  level_distribution: Record<string, number>;
  market_signal: {
    graph_layer: string;
    is_probability: boolean;
    base_weight: number;
    delta_weight: number;
    effective_weight: number;
  };
}

export interface JobSummaryResponse {
  schema_version: 'aggregated_job_summary_v1';
  source_type: 'aggregated_job_summary';
  window: string;
  job: { job_code: string; job_name: string; jd_count: number };
  skills: JobSummarySkill[];
}

/** 岗位一侧的一项能力要求 */
export interface TargetJobSkill {
  team_skill_id: string;
  team_skill_name: string;
  provider_skill_name: string;
  skill_type: 'hard' | 'soft';
  is_primary: boolean;
  requirement_present: boolean;
  required_level_raw: string | null;
  required_level: ProficiencyLevel | null;
  /** EXPLICIT_LEVEL / LEVEL_UNSPECIFIED 进入计分，其余两态不进入 */
  requirement_status:
    | 'EXPLICIT_LEVEL'
    | 'LEVEL_UNSPECIFIED'
    | 'PROFICIENCY_NOT_AVAILABLE'
    | 'AUXILIARY_NOT_GRADED';
  learning_path_target_eligible: boolean;
  level_comparison_eligible: boolean;
  requirement_evidence_kind: string;
  requirement_evidence_ref: string;
  /**
   * 这一项要求由哪些原始计数得来。岗位级基准独有（单条招聘信息无从统计）。
   *
   * 报告里凡是要回答"凭什么说这个岗位要求这项能力、要到什么份上"，都落在这里：
   * 提及条数与占比说明它有多普遍，熟练度分布说明要求的档位是怎么定出来的。
   */
  requirement_statistics?: {
    jd_count: number;
    jd_presence_count: number;
    jd_presence_rate: number;
    level_distribution: Record<string, number>;
    /** 提到这一项的条目里，写明了熟练度的条数 */
    graded_posting_count: number;
    graded_ratio: number;
    level_rule: string;
  };
  market_signal: {
    graph_layer: string;
    is_probability: boolean;
    origin: string;
    base_weight: number;
    delta_weight: number;
    effective_weight: number;
    gap: number;
    weight: number;
    lambda: number;
  };
}

export interface TargetJobProfile {
  schema_version: 'target_job_profile_v1.1';
  /**
   * 基准的取数范围。
   *
   * `single_jd`       某一条招聘信息。
   * `aggregated_job`  该岗位在本窗口内的全部招聘信息，逐条统计后按能力归并。
   *
   * 本页取后者：求职者要判断的是与这个岗位的差距，不是与某一条启事的差距。
   */
  source_type: 'single_jd' | 'aggregated_job';
  window: string;
  job: {
    job_code: string;
    job_name: string;
    jd_key: string | null;
    jobid: string;
    title: string;
    std_job: string;
    opentime: string | null;
    level: string | null;
    level_source: string;
    techstack: string | null;
    /** 岗位级基准独有：这份基准由多少条招聘信息汇总而来 */
    aggregated?: boolean;
    jd_count?: number;
  };
  taxonomy: Record<string, unknown>;
  source_provenance: Record<string, unknown>;
  semantics: Record<string, unknown>;
  skills: TargetJobSkill[];
  warnings: string[];
}

export interface MatchSkillItem {
  team_skill_id: string;
  team_skill_name: string;
  required_level: ProficiencyLevel | null;
  candidate_level: ProficiencyLevel | null;
  gap_type: GapType;
  path_mode: PathMode;
  requirement_type: string;
  requirement_evidence: string[];
  candidate_evidence: Array<{
    evidence_ref?: string;
    text?: string;
    source_id?: string;
    start?: number | null;
    end?: number | null;
  }>;
  explanation: string;
}

export interface MatchResultV1 {
  schema_version: 'match_result_v1';
  candidate_id: string;
  job_id: string;
  job_title: string;
  /** 0–100，口径为“已验证满足的岗位要求 / 可计分的岗位要求”，不是相似度 */
  match_score: number;
  decision: MatchDecision;
  decision_threshold: number | null;
  summary: {
    required_skills: number;
    satisfied: number;
    level_gap: number;
    evidence_insufficient: number;
    missing: number;
  };
  metrics: {
    verified_fit: number;
    skill_coverage: number;
    level_gap_rate: number;
    uncertainty_rate: number;
    missing_rate: number;
  };
  skills: MatchSkillItem[];
  semantics?: Record<string, unknown>;
}

export interface ProficiencyBlock {
  source: 'provided' | 'auto_on_demand' | 'not_run';
  levels: Record<string, ProficiencyLevel>;
  details: Array<Record<string, unknown>>;
}

export interface MatchingApiResponse {
  schema_version: 'matching_pipeline_output_v1';
  match_result: MatchResultV1;
  diagnostics: Record<string, unknown>;
  target_job_profile: TargetJobProfile;
  proficiency: ProficiencyBlock;
}

export interface LearningStep {
  node_id: string;
  node_name: string;
  reason: string;
  evidence_task: string;
  validation_criteria: string[];
}

export interface SkillLearningPath {
  team_skill_id: string;
  team_skill_name: string;
  gap_type: GapType;
  observed_level: ProficiencyLevel | null;
  required_level: ProficiencyLevel | null;
  path_mode: PathMode;
  achieved_node_ids: string[];
  current_state: string;
  gap_explanation: string;
  development_goal: string;
  learning_steps: LearningStep[];
  specialization_extensions: unknown[];
  /** VERIFY_FIRST 模式独有：一项用于补足可判定等级之证据的验证任务 */
  verification_guidance: {
    task_id: string;
    task_name: string;
    task_description: string;
    validation_criteria: string[];
    source_references: unknown[];
  } | null;
  capstone_guidance: {
    task_id: string;
    objective: string;
    task_description: string;
    validation_criteria: string[];
    purpose: string;
  } | null;
  reassessment_required: boolean;
  reassessment_guidance: string;
  /** READY 才有可渲染的步骤；GRAPH_UNAVAILABLE 表示该技能尚无 curated 图谱 */
  path_status: 'READY' | 'NO_ACTION' | 'GRAPH_UNAVAILABLE' | string;
  render_status: string;
}

export interface LearningPathApiResponse {
  schema_version: 'learning_path_api_response_v1';
  path_status: string;
  gap_summary: {
    total_requirements: number;
    MISSING: number;
    LEVEL_GAP: number;
    EVIDENCE_INSUFFICIENT: number;
    SATISFIED: number;
  };
  rendered: {
    candidate_id: string;
    target_job_id: string;
    skill_paths: SkillLearningPath[];
    render_status: string;
  };
  proficiency: ProficiencyBlock;
  diagnostics: Record<string, unknown> & { curated_graph_count?: number };
}

export interface HealthResponse {
  status: 'ok';
  service: string;
  candidate_runtime: string;
  target_job_schema: string;
  matching_schema: string;
  /** 二分类阈值尚未标定时为 false，此时 decision 恒为 NOT_CALIBRATED */
  matching_calibrated: boolean;
  window: string;
}

/* ---------------- 客户端 ---------------- */

const RAW = import.meta.env.VITE_MATCH_API;
export const MATCH_API = typeof RAW === 'string' ? RAW.trim().replace(/\/+$/, '') : '';
/** 后端地址已配置。可达与否要等 health 探测，见 useMatchBackend */
export const LIVE_MATCH = MATCH_API.length > 0;

/** 后端窗口固定为 2022-10：岗位一侧的 JD 均取自该窗口 */
export const MATCH_WINDOW = '2022-10';

export class MatchApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = 'MatchApiError';
  }
}

/** 后端出错时返回 { detail }，把它取出来当消息，取不到就退回状态码 */
async function toError(r: Response): Promise<MatchApiError> {
  let detail = `HTTP ${r.status}`;
  try {
    const body = await r.json();
    if (body && typeof body.detail === 'string') detail = body.detail;
    else if (Array.isArray(body?.detail)) detail = JSON.stringify(body.detail);
  } catch {
    /* 后端异常时可能不是 JSON，保留状态码即可 */
  }
  return new MatchApiError(r.status, detail);
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${MATCH_API}${path}`, { signal });
  if (!r.ok) throw await toError(r);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${MATCH_API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) throw await toError(r);
  return r.json() as Promise<T>;
}

export const fetchHealth = (signal?: AbortSignal) => get<HealthResponse>('/health', signal);

export const fetchJobIndex = (signal?: AbortSignal) => get<JobIndexResponse>('/api/job-index', signal);

/** 按标准岗位名取该岗位在本窗口下的 JD 列表 */
export const fetchJdList = (stdJob: string, limit = 40, signal?: AbortSignal) =>
  get<JobCatalogResponse>(
    `/api/jobs?std_job=${encodeURIComponent(stdJob)}&limit=${limit}`,
    signal,
  );

/** 岗位在本窗口内的能力要求汇总。窗口内没有该岗位样本时后端回 404 */
export const fetchJobSummary = (jobCode: string, signal?: AbortSignal) =>
  get<JobSummaryResponse>(`/api/job-summary/${encodeURIComponent(jobCode)}`, signal);

export const fetchTargetJob = (jobId: string, jdKey?: string, signal?: AbortSignal) =>
  get<TargetJobProfile>(
    `/api/target-job/${encodeURIComponent(jobId)}${jdKey ? `?jd_key=${encodeURIComponent(jdKey)}` : ''}`,
    signal,
  );

/** 只看解析质量，不调用 LLM。上传后先跑这一步，解析不过关就不必再往下走 */
export async function preflightResume(file: File, signal?: AbortSignal): Promise<CandidatePreflight> {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`${MATCH_API}/api/candidate?preflight=true`, {
    method: 'POST',
    body: fd,
    signal,
  });
  if (!r.ok) throw await toError(r);
  return r.json();
}

/**
 * 简历抽取。后端为子进程同步跑冻结管线，需要 LLM，耗时以分钟计。
 *
 * allowLowQuality 对应后端的 --allow-low-quality-parser：解析质量门未通过时，
 * 后端默认拒绝把低质量文本送入模型（返回 502），置真则照常送入。
 * 这一位不应由前端替使用者决定，须由使用者在看过预检结果后自行选择。
 */
export async function extractCandidate(
  file: File,
  candidateId?: string,
  signal?: AbortSignal,
  allowLowQuality = false,
): Promise<CandidateApiResponse> {
  const fd = new FormData();
  fd.append('file', file);
  if (candidateId) fd.append('candidate_id', candidateId);
  const url = `${MATCH_API}/api/candidate${allowLowQuality ? '?allow_low_quality_parser=true' : ''}`;
  const r = await fetch(url, { method: 'POST', body: fd, signal });
  if (!r.ok) throw await toError(r);
  return r.json();
}

/** 解析质量的各项判据。后端只给旗标名，中文与其含义在此对齐 */
export const QUALITY_FLAG_TEXT: Record<string, string> = {
  too_little_text: '可提取的文字过少（不足 80 字）',
  too_few_nonempty_lines: '非空行不足 3 行',
  low_readable_char_ratio: '可读字符占比过低，多半是编码或字体嵌入的问题',
  replacement_character_present: '文本中含替换字符，即已出现乱码',
  pdf_many_text_empty_pages: '过半页面提取不到文字，多半是扫描件或图片版',
  suspicious_fragmented_lines: '过半的行只有一两个字，版面还原很可能失败（多见于两栏或图形化排版）',
};

export interface MatchRequestBody {
  candidate_profile: CandidateSkillProfile;
  job_id?: string;
  jd_key?: string;
  /** 标准岗位编码（AID-01）。给它即以该岗位窗口内全部招聘信息的汇总为基准 */
  job_code?: string;
  target_job_profile?: TargetJobProfile;
  proficiency_levels?: Record<string, ProficiencyLevel>;
  /** 为 true 时后端逐项调 LLM 评级，另有延迟与费用 */
  auto_proficiency?: boolean;
  /**
   * 评级范围。
   *
   * `target`    只给目标岗位要求的那几项定级；换一个岗位就换一批，每换一次都要重跑。
   * `candidate` 给简历已具备的全部能力定级，与目标岗位无关，一次算齐即可反复使用。
   *
   * 本页取后者：定级逐项调模型、一项数十秒，而报告页允许随时改选目标岗位。
   * 一次算齐之后，换岗位只需把已得的档位随请求带上并关掉自动定级，
   * 比对本身是纯规则运算，即时可得。
   */
  proficiency_scope?: 'target' | 'candidate';
}

export const runMatch = (body: MatchRequestBody, signal?: AbortSignal) =>
  post<MatchingApiResponse>('/api/match', body, signal);

export const runLearningPath = (body: MatchRequestBody, signal?: AbortSignal) =>
  post<LearningPathApiResponse>('/api/learning-path', body, signal);
