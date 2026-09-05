# -*- coding: utf-8 -*-
"""Builder 配置：API、路径、采样/迭代参数。

可调参数统一读取全局参数中心 `codes/settings.yaml`（按路径读文件，与 import 顺序无关），
本文件保留原变量名作薄读取层，内置同值默认兜底；只调整路径类常量与模块私有产物位置。
体系基准（tasks/skills/jobs 标签源）另经 `classify/taxonomy_base.json` 单一开关切换。
"""
import json
import os

import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_SETTINGS_PATH = os.path.join(PROJECT_ROOT, "codes", "settings.yaml")


def _settings(*keys, default):
    """从全局参数中心读取（逐级下钻）；文件缺失/损坏/键不存在回退 default。"""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return default


def _taxonomy_base(key, default_rel):
    """体系基准（标签源）路径：classify/taxonomy_base.json 单一开关 > 环境变量 > 内置默认。

    - taxonomy_base.json 条目为相对 classify/ 的路径（改它即全局切换基准，无需改代码）
    - 环境变量 TAXONOMY_BASE_{TASKS|SKILLS|JOBS} 临时覆盖单项（当次运行生效，适合实验对比）
    """
    root = os.path.join(PROJECT_ROOT, "classify")
    rel = default_rel
    try:
        with open(os.path.join(root, "taxonomy_base.json"), encoding="utf-8") as f:
            rel = json.load(f).get(key) or rel
    except (OSError, ValueError):
        pass  # 无开关文件/损坏 → 用内置默认
    p = os.environ.get("TAXONOMY_BASE_" + key.upper()) or rel
    return p if os.path.isabs(p) else os.path.join(root, p)


# ---------------- LLM API（settings.yaml → llm） ----------------
API_URL = _settings("llm", "api_url", default="https://api.deepseek.com/chat/completions")
DEFAULT_MODEL = _settings("llm", "model", default="deepseek-v4-flash")
KEY_FILE = os.path.join(PROJECT_ROOT, _settings("llm", "key_file", default="codes/api-key.txt"))
# 并行 api-key 数（多 key 请求级轮转；总批次并发 = llm.concurrency × 实际启用数）
API_KEYS_PARALLEL = int(_settings("llm", "api_keys_parallel", default=1) or 1)
# key 预检：KeyRing 首次构建时并行探测启用 key，不可用者本进程内剔除（false=跳过）
KEY_PROBE = bool(_settings("llm", "key_probe", default=True))
# True=禁用推理（v4-flash 为推理模型，不关时 reasoning 会烧光 max_tokens 截断 JSON）
USE_THINKING = _settings("llm", "use_thinking", default=True)

# ---------------- 分类体系（基准经 classify/taxonomy_base.json 切换，此处为兜底默认） ----------------
# 任务体系输出路径（Builder 构建/更新的产物）
TASK_TAXONOMY = _taxonomy_base("tasks", os.path.join("Tasks", "tasks.json"))
# 技能体系标签源（当前标准，2026-08-21 起：命名规范化版 skills0821.json——20 项 name_zh 更名、
# 编码/定义不变；前版 skills0805.json 为 2026-08-16 起的文献梳理版）。
# 背景：论文试跑发现映射基准为 Builder 版时会漏判——PS-001「大语言模型幻觉防控」与文献版
# T-AI-13（现名「AI幻觉识别与校验」）语义等价却被判"新技能"。两版并存期间以文献版为唯一标准。
SKILL_TAXONOMY = _taxonomy_base("skills", os.path.join("Skills", "skills0821.json"))
# Builder 归纳版产物（JD 冷启动+热更新输出路径；非当前标准，留作对比与后续合并）
SKILL_BUILDER_OUTPUT = os.path.join(PROJECT_ROOT, "classify", "Skills", "skills_builder.json")
# 岗位体系（jobs_v2.json，v2.0 9 类别 131 岗位，2026-08-22 起为运行时基准；只读标签源，
# 转正 promotion 追加 GJ- 条目；v1 jobs0806.json 255 岗位保留存档）
JOB_TAXONOMY = _taxonomy_base("jobs", os.path.join("Jobs", "jobs_v2.json"))

# ---------------- 论文 ΔG 增量层（热更新产物） ----------------
DELTA_OUTPUT = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "papers_delta.json")
DELTA_CHECKPOINT = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "papers_delta_checkpoint.json")
DELTA_LOG = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "paper_signal_log.jsonl")
DELTA_LOG_MD = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "paper_signal_log.md")
# 单次提取 LLM 调用包含的论文数（论文信号流水线）
EXTRACT_CHUNK = _settings("papers", "extract_chunk", default=3)

# ---------------- 新闻 ΔG 增量层（热更新产物） ----------------
NEWS_DELTA_OUTPUT = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "news_delta.json")
NEWS_DELTA_CHECKPOINT = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "news_delta_checkpoint.json")
NEWS_DELTA_LOG = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "news_log.jsonl")
NEWS_DELTA_LOG_MD = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "news_log.md")
NEWS_EXTRACT_CHUNK = _settings("news", "extract_chunk", default=3)

# ---------------- JD 侧 ΔG 增量层（市场确证热更新产物） ----------------
JD_DELTA_OUTPUT = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "jd_delta.json")
JD_DELTA_CHECKPOINT = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "jd_delta_checkpoint.json")
JD_DELTA_LOG = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "jd_log.jsonl")
JD_DELTA_LOG_MD = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "jd_log.md")
JD_EXTRACT_CHUNK = _settings("jd", "extract_chunk", default=3)
JD_SAMPLE_TOTAL = _settings("jd", "sample_total", default=100)
JD_PER_FUNTYPE = _settings("jd", "per_funtype", default=3)
JD_EXTRACT_BODY_CHARS = _settings("jd", "extract_body_chars", default=2000)
JD_MIN_TEXT_CHARS = _settings("jd", "min_text_chars", default=100)

# 三源 ΔG 文件汇总（participation/promotion 等消费；graph 侧另有同名透传）
DELTA_FILES = {"papers": DELTA_OUTPUT, "news": NEWS_DELTA_OUTPUT, "jd": JD_DELTA_OUTPUT}

# ---------------- 叠层生命周期（可见性 / 遗忘 / 转正） ----------------
OVERLAY_PARTICIPATE_MIN = _settings("overlay", "participate_min_strength", default=0.15)
OVERLAY_PROMOTE_MIN_STRENGTH = _settings("overlay", "promote_min_strength", default=0.25)
OVERLAY_PROMOTE_MIN_STRENGTH_JOBS = _settings("overlay", "promote_min_strength_jobs", default=0.30)
OVERLAY_PROMOTE_MIN_JD_DOCS = _settings("overlay", "promote_min_jd_docs", default=2)
OVERLAY_PROMOTE_MIN_JD_DOCS_JOBS = _settings("overlay", "promote_min_jd_docs_jobs", default=3)
OVERLAY_PROMOTION_LOG = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "promotion_log.md")
OVERLAY_BACKUP_DIR = os.path.join(PROJECT_ROOT, "classify", "backup")

# ---------------- 强度聚合（settings.yaml → strength） ----------------
TIER_WEIGHTS = _settings("strength", "tier_weights", default={"S": 1.0, "A": 0.7, "B": 0.5, "C": 0.3, "": 0.2})
CONF_WEIGHT = _settings("strength", "conf_weight", default={"high": 1.0, "medium": 0.6, "low": 0.3})
HALF_LIFE_DAYS = _settings("strength", "paper_half_life_days", default=730)
NEWS_SOURCE_WEIGHT = _settings("strength", "news_source_weight", default=0.4)
# 新闻月度降采样：单窗处理上限（超出部分均匀随机抽样；窗口种子确定性，可重演）
NEWS_SAMPLE_CAP = _settings("news_delta", "sample_cap", default=800)
NEWS_DERIVED_DIR = os.path.join(PROJECT_ROOT, "data", "timeline", "news_derived")
NEWS_HALF_LIFE_DAYS = _settings("strength", "news_half_life_days", default=180)
JD_SOURCE_WEIGHT = _settings("strength", "jd_source_weight", default=1.0)
JD_HALF_LIFE_DAYS = _settings("strength", "jd_half_life_days", default=365)
RECENCY_UNKNOWN_DECAY = _settings("strength", "recency_unknown_decay", default=0.5)
MIN_STRENGTH = _settings("strength", "min_strength", default=0.05)

# ---------------- 运行跟踪日志（人类可读 + 结构化） ----------------
# 记录冷启动/热更新提案/监督/应用/重检全过程，供人类专家检验与膨胀归因
BUILDER_LOG = os.path.join(PROJECT_ROOT, "classify", "Tasks", "builder_log.jsonl")
BUILDER_LOG_MD = os.path.join(PROJECT_ROOT, "classify", "Tasks", "builder_log.md")
SKILL_BUILDER_LOG = os.path.join(PROJECT_ROOT, "classify", "Skills", "builder_log.jsonl")
SKILL_BUILDER_LOG_MD = os.path.join(PROJECT_ROOT, "classify", "Skills", "builder_log.md")

# ---------------- 岗位热更新（关联分析；文本预算 settings.yaml → builder） ----------------
JOB_ASSOC_MAX_CHARS = _settings("builder", "job_assoc_max_chars", default=6000)
JOB_HOT_LOG = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "job_hot_update_log.jsonl")
JOB_HOT_LOG_MD = os.path.join(PROJECT_ROOT, "classify", "DeltaG", "job_hot_update_log.md")

# ---------------- JD 数据源 ----------------
JD_CSV_DIR = os.path.join(PROJECT_ROOT, "data", "jd_dataset")

# ---------------- 抽样/迭代参数（settings.yaml → builder） ----------------
COLD_SAMPLE = _settings("builder", "cold_sample", default=500)
HOT_BATCH = _settings("builder", "hot_batch", default=200)
HOT_CHUNK = _settings("builder", "hot_chunk", default=50)
MIN_PER_STRATUM = _settings("builder", "min_per_stratum", default=3)
MAX_ROUNDS = _settings("builder", "max_rounds", default=5)
MAX_RECHECK = _settings("builder", "max_recheck", default=3)
DOC_MAX_CHARS = _settings("builder", "doc_max_chars", default=1200)
COLD_MIN_TASKS = _settings("builder", "cold_min_tasks", default=8)
COLD_MAX_TASKS = _settings("builder", "cold_max_tasks", default=30)
SKILL_MIN_SKILLS = _settings("builder", "skill_min_skills", default=30)
SKILL_MAX_SKILLS = _settings("builder", "skill_max_skills", default=60)

# ---------------- 分层抽样参考 ----------------
# 岗位分类体系（jobs_v2.json）；JD 分层实际走 data_source.JDDataSource 的 v2 直连口径
JOBS_TAXONOMY = JOB_TAXONOMY

# ---------------- LLM 调用（settings.yaml → llm） ----------------
BATCH_MAX_TOKENS = _settings("llm", "batch_max_tokens", default=8000)
MAX_TOKENS_CAP = _settings("llm", "max_tokens_cap", default=32000)
TIMEOUT = _settings("llm", "timeout", default=180)
RETRIES = _settings("llm", "retries", default=3)


def load_api_keys():
    """读取全部可用 key（去重保序）：codes/api-key.txt（正则全量提取 sk-…，每行一个）
    + 环境变量（DEEPSEEK_API_KEY/ANTHROPIC_AUTH_TOKEN，逗号/分号/空白分隔多值）。"""
    import re
    keys = []
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            keys.extend(re.findall(r"sk-[A-Za-z0-9]+", f.read()))
    for env in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        for v in re.split(r"[,;\s]+", os.environ.get(env, "").strip()):
            if v:
                keys.append(v)
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def active_api_keys():
    """并行启用集：全部 key 的前 llm.api_keys_parallel 个（key 文件不足时以实际数为准）。
    多 key 请求级轮转分摊限速；单 key（或开关=1）行为与旧版完全一致。"""
    keys = load_api_keys()
    return keys[:max(1, API_KEYS_PARALLEL)] if keys else []


def load_api_key():
    """单 key 兼容（历史调用方）：首个可用 key。"""
    return (load_api_keys() or [""])[0]
