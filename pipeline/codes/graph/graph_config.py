# -*- coding: utf-8 -*-
"""图谱时间截面快照（graph）配置：路径常量与输出文件名。

存储机制：`data/graph/{窗口}/` 下每个时间截面（月 `YYYY-MM` 或季度 `YYYY-Qn`）一个文件夹，
内含 `base/`（基图）与 `delta/`（叠层）两个子图；节点用体系 JSON，关系用边 JSON（每种连边一个文件）。

复用 `codes/builder/config.py` 的路径与强度权重常量（跨模块 sys.path 引入，沿用既有约定）：
- 基图节点源：jobs_v2.json（v2.0，2026-08-22 起为运行时基准）/ tasks.json / skills0821.json（技能体系当前标准）
- 叠层源：papers_delta.json / news_delta.json
- 强度重算权重：TIER_WEIGHTS / CONF_WEIGHT / 半衰期 / MIN_STRENGTH
"""
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 复用 builder 的路径与权重常量（builder/config.py 与 delta_store.py 同目录）
_BUILDER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "builder"))
if _BUILDER_DIR not in sys.path:
    sys.path.insert(0, _BUILDER_DIR)
import config  # noqa: E402


def _settings(*keys, default):
    """从全局参数中心 codes/settings.yaml 读取（逐级下钻）；缺失/损坏回退 default。

    与 builder/config.py 的读取层同模式：按路径读文件而非 import，
    免疫跨模块 sys.modules 缓存顺序问题。
    """
    try:
        import yaml
        with open(os.path.join(PROJECT_ROOT, "codes", "settings.yaml"), encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return default

# ---------------- 输出根目录 ----------------
GRAPH_ROOT = os.path.join(PROJECT_ROOT, "data", "graph")   # 已 gitignore，可脚本重建

# ---------------- 子目录 ----------------
BASE_SUBDIR = "base"
DELTA_SUBDIR = "delta"
META_FILENAME = "meta.json"

# ---------------- 基图节点源（体系 JSON） ----------------
BASE_NODE_FILES = {
    "jobs": config.JOB_TAXONOMY,
    "tasks": config.TASK_TAXONOMY,
    "skills": config.SKILL_TAXONOMY,
}

# ---------------- 叠层源（ΔG 增量层三源：论文 + 新闻 + JD 确证） ----------------
DELTA_FILES = {
    "papers": config.DELTA_OUTPUT,
    "news": config.NEWS_DELTA_OUTPUT,
    "jd": config.JD_DELTA_OUTPUT,
}

# ---------------- 基图边类型（每种边一个文件；本阶段建空 schema，由图谱构建任务填充） ----------------
BASE_EDGE_KINDS = ("job_task", "job_skill", "task_skill", "skill_skillpoint")

# ---------------- 输出文件名 ----------------
BASE_NODE_FILENAMES = {
    "jobs": "jobs.json", "tasks": "tasks.json", "skills": "skills.json", "skillpoints": "skillpoints.json",
}
DELTA_NODE_FILENAMES = {
    "new_jobs": "new_jobs.json", "new_tasks": "new_tasks.json",
    "new_skills": "new_skills.json", "skillpoints": "skillpoints.json",
}
EDGE_FILENAMES = {**{k: f"{k}.json" for k in BASE_EDGE_KINDS},
                  "strengthenings": "strengthenings.json", "job_links": "job_links.json"}

# ---------------- 强度常量（透传自 builder config，供快照重算） ----------------
TIER_WEIGHTS = config.TIER_WEIGHTS
CONF_WEIGHT = config.CONF_WEIGHT
HALF_LIFE_DAYS = config.HALF_LIFE_DAYS
NEWS_SOURCE_WEIGHT = config.NEWS_SOURCE_WEIGHT
NEWS_HALF_LIFE_DAYS = config.NEWS_HALF_LIFE_DAYS
JD_SOURCE_WEIGHT = config.JD_SOURCE_WEIGHT
JD_HALF_LIFE_DAYS = config.JD_HALF_LIFE_DAYS
RECENCY_UNKNOWN_DECAY = config.RECENCY_UNKNOWN_DECAY
MIN_STRENGTH = config.MIN_STRENGTH

# ---------------- 叠层生命周期阈值（settings.yaml → overlay） ----------------
OVERLAY_PARTICIPATE_MIN = config.OVERLAY_PARTICIPATE_MIN
OVERLAY_PROMOTE_MIN_STRENGTH = config.OVERLAY_PROMOTE_MIN_STRENGTH
OVERLAY_PROMOTE_MIN_STRENGTH_JOBS = config.OVERLAY_PROMOTE_MIN_STRENGTH_JOBS
OVERLAY_PROMOTE_MIN_JD_DOCS = config.OVERLAY_PROMOTE_MIN_JD_DOCS
OVERLAY_PROMOTE_MIN_JD_DOCS_JOBS = config.OVERLAY_PROMOTE_MIN_JD_DOCS_JOBS

# ---------------- 强度权重快照（写入 meta，保证重算可复现） ----------------
WEIGHTS = {
    "TIER_WEIGHTS": TIER_WEIGHTS,
    "CONF_WEIGHT": CONF_WEIGHT,
    "HALF_LIFE_DAYS": HALF_LIFE_DAYS,
    "NEWS_SOURCE_WEIGHT": NEWS_SOURCE_WEIGHT,
    "NEWS_HALF_LIFE_DAYS": NEWS_HALF_LIFE_DAYS,
    "JD_SOURCE_WEIGHT": JD_SOURCE_WEIGHT,
    "JD_HALF_LIFE_DAYS": JD_HALF_LIFE_DAYS,
    "RECENCY_UNKNOWN_DECAY": RECENCY_UNKNOWN_DECAY,
    "MIN_STRENGTH": MIN_STRENGTH,
    "OVERLAY_PARTICIPATE_MIN": OVERLAY_PARTICIPATE_MIN,
}

# ---------------- 基图边计算参数（settings.yaml → graph_base） ----------------
GB_SAMPLE_TOTAL = _settings("graph_base", "sample_total", default=200)
GB_PER_JOB = _settings("graph_base", "per_job", default=5)
GB_MIN_TEXT_CHARS = _settings("graph_base", "min_text_chars", default=100)
GB_SALARY_WEIGHT = _settings("graph_base", "salary_weight", default=False)
GB_ALPHA = _settings("graph_base", "alpha", default=0.85)
GB_TS_W1 = _settings("graph_base", "ts_w1", default=1.0)
GB_TS_W2 = _settings("graph_base", "ts_w2", default=0.0)
GB_WRITE_JD_VECTORS = _settings("graph_base", "write_jd_vectors", default=True)


# ---------------- 组装期参数指纹（重放可追溯，2026-08-27 起） ----------------
# 纯组装参数（改后零 LLM 重放即生效：D 聚合 / 快照强度重算 / 合成）统一取指纹写入
# base/build_info、快照 meta、effective/meta——重放（replay.py）与人工核对都能看出
# 每份产物是哪套参数算出来的。ASSEMBLY_LOGIC_VERSION 在硬编码组装逻辑（边收录规则、
# gap 公式、合成方式等非参数中心部分）变更时手工步进。
# v2（2026-08-29）：快照合并传播 born_window（入场窗，确证滞后语义的判定基准）+
# build_snapshot 显式 delta_files 即完整规格（缺键=该源缺席，不再兜底读生产文件）。
ASSEMBLY_LOGIC_VERSION = "v2"


def assembly_params_fingerprint():
    """settings.yaml 组装四节点 + 逻辑版本 → 16 位十六进制指纹。"""
    import hashlib
    payload = {
        "logic": ASSEMBLY_LOGIC_VERSION,
        "strength": _settings("strength", default={}),
        "overlay": _settings("overlay", default={}),
        "graph_base": _settings("graph_base", default={}),
        "synthesis": _settings("synthesis", default={}),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

# ---------------- jd_vectors 源文件（Stage B 产、Stage D 基图聚合消费） ----------------
# data/timeline/jd_derived/{window}.jd_vectors.jsonl：每 JD 一条分类向量记录（Stage A 岗位归类 +
# Stage B 句级抽取），含 skill_vec_01 / task_vec_01 / skillpoint_map / evidence_map /
# job_code / salary_weight / sample_weight；Stage D base_builder 读其消费重建 per-JD 集合做边聚合，
# 不再走字符串 funtype 门与抽样。.meta.json 记 taxonomy sha256/rubric_version 供复用校验。
# jd_derived/ 与源数据 jd/（月度 CSV）分离：同目录还有 {window}.sample.json（Stage S）与
# {window}.jobcls.json（A 门窗口归类缓存）——timeline/jd 只放源 CSV。
TIMELINE_JD_DIR = os.path.join(PROJECT_ROOT, "data", "timeline", "jd")
JD_DERIVED_DIR = os.path.join(PROJECT_ROOT, "data", "timeline", "jd_derived")
JD_VECTORS_FILENAME = "{window}.jd_vectors.jsonl"
JD_VECTORS_META_FILENAME = "{window}.jd_vectors.meta.json"

# ---------------- Stage S 降采样（jd_sample.py 产、run_jd_extract 消费） ----------------
# data/timeline/jd_derived/{window}.sample.json：窗口内 IT JD 的分层封顶抽样键集 + 逆概率权重。
# 存在且 keys 非空 → Stage B 只抽采样键并写入 sample_weight；rate=1 时 keys=null（不过滤，
# 文件仍记录各岗位层分母供时序分析）。参数在 settings.yaml → jd_sampling（cap/floor/salt）。
JD_SAMPLE_FILENAME = "{window}.sample.json"
# 默认参数（jd_sample.py 与 settings 兜底同源；cap=窗口 IT 保留上限，floor=稀疏岗保底）
JD_SAMPLE_CAP = _settings("jd_sampling", "cap", default=10000)
JD_SAMPLE_FLOOR = _settings("jd_sampling", "floor", default=30)
JD_SAMPLE_SALT = _settings("jd_sampling", "salt", default="challenge26-jd-sampling-v1")

# ---------------- Stage S0 预抽样（jd_pre_sample.py 产、classify_job 消费，2026-09-03） ----------------
# 大窗 unique 指纹 > presample_cap 时按确定性哈希选 presample_cap 个（md5 升序），
# A 门只归类已选键 → jobcls 只含已选键 → S/D0/B/v2 经 load_full_classification 全链自动
# 受限；w0=N/k 由 Stage S 复合进逆概率权重（窗口总量/边权无偏）。
# 校准依据（真实记录数，wc -l 因内嵌换行虚高 ~14×）：2026-01 29k / 02 41k / 03 101k
# （unique 指纹 27k/37k/~95k）——cap 60k 恰只让 2026-03 触发（~63% 选择率），
# 02 及以下全量保真；历史最大 2022-07（27.3 万 unique）当时全量跑通，60k 远在其下。
JD_PRESAMPLE_FILENAME = "{window}.presample.json"
JD_PRESAMPLE_CAP = _settings("jd_sampling", "presample_cap", default=60000)
JD_PRESAMPLE_SALT = _settings("jd_sampling", "presample_salt", default="challenge26-jd-presample-v1")

# ---------------- Stage D0 近重复（抄袭）过滤（jd_dedup.py 产，S/B/D/v2 消费） ----------------
# data/timeline/jd_derived/{window}.dedup.json：simhash+Jaccard 抄袭簇的变体键→代表键映射。
# 消费方在线过滤（产物缺失=无操作，向后兼容）；参数在 settings.yaml → jd_dedup。
JD_DEDUP_FILENAME = "{window}.dedup.json"

# ---------------- 图谱合成参数（settings.yaml → synthesis） ----------------
SYN_LAMBDA_J = _settings("synthesis", "lambda_j", default=0.3)
SYN_LAMBDA_TS = _settings("synthesis", "lambda_ts", default=0.3)
SYN_LAMBDA_SP = _settings("synthesis", "lambda_sp", default=0.3)
SYN_MAX_NEW_TS_EDGES = _settings("synthesis", "max_new_ts_edges", default=100)

# 合成参数快照（写入 effective/meta.json，保证重算可复现）
SYN_WEIGHTS = {
    "lambda_j": SYN_LAMBDA_J,
    "lambda_ts": SYN_LAMBDA_TS,
    "lambda_sp": SYN_LAMBDA_SP,
    "max_new_ts_edges": SYN_MAX_NEW_TS_EDGES,
}

# ---------------- effective 子目录与文件名（图谱合成 G_eff 独立存储层） ----------------
EFFECTIVE_SUBDIR = "effective"
EFFECTIVE_EDGE_NAMES = {
    "job_task": "关系：岗位→任务（合成 G_eff）",
    "job_skill": "关系：岗位→技能（合成 G_eff）",
    "task_skill": "关系：任务→技能（合成 G_eff）",
    "skill_skillpoint": "关系：技能→技能点（合成 G_eff）",
}
# 基图边计算的附属产物（写在 base/ 下，快照重建时同样受 keep_base_edges 保护）
BASE_AUX_FILENAMES = {
    "freq": "freq.json",            # 衰减后加权频次（下窗口 α 累积链）
    "entity_freq": "entity_freq.json",  # 实体文档频率 E_jd（合成 gap 用）
    "skill_prof": "skill_prof.json",    # 技能熟练度要求分布（jd_proficiency 聚合，演化分析用）
    "build_info": "build_info.json",    # 抽样/参数/统计记录
}
