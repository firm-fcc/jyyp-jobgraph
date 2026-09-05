# -*- coding: utf-8 -*-
"""Extractor 全局配置：API、路径、批处理参数。

可调参数统一读取全局参数中心 `codes/settings.yaml`（按路径读文件，与 import 顺序无关，
与 builder/config.py 同源——LLM/API 等共享参数不再两份拷贝各自维护），
本文件保留原变量名作薄读取层，内置同值默认兜底。
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


# ---------------- LLM API（settings.yaml → llm，与 builder 同源） ----------------
API_URL = _settings("llm", "api_url", default="https://api.deepseek.com/chat/completions")
DEFAULT_MODEL = _settings("llm", "model", default="deepseek-v4-flash")
KEY_FILE = os.path.join(PROJECT_ROOT, _settings("llm", "key_file", default="codes/api-key.txt"))
# 禁用推理：v4-flash 为推理模型，不关时 reasoning 会烧光 max_tokens 导致 JSON 截断
USE_THINKING = _settings("llm", "use_thinking", default=True)

# ---------------- 分类体系文件（基准经 classify/taxonomy_base.json 切换，此处为兜底默认） ----------------
def _taxonomy_base(key, default_rel):
    """体系基准路径：classify/taxonomy_base.json 单一开关 > 环境变量 > 内置默认（与 builder 同一份开关）。"""
    root = os.path.join(PROJECT_ROOT, "classify")
    rel = default_rel
    try:
        with open(os.path.join(root, "taxonomy_base.json"), encoding="utf-8") as f:
            rel = json.load(f).get(key) or rel
    except (OSError, ValueError):
        pass
    p = os.environ.get("TAXONOMY_BASE_" + key.upper()) or rel
    return p if os.path.isabs(p) else os.path.join(root, p)


# 技能体系标签源（当前标准，2026-08-21 起：命名规范化版 skills0821.json，仅 20 项 name_zh 更名、
# 编码/定义不变；前版 skills0805.json 为 2026-08-16 起的文献梳理版；
# skills_builder.json 为 Builder 归纳产物，参考/后续合并，非标签源）
SKILL_TAXONOMY = _taxonomy_base("skills", os.path.join("Skills", "skills0821.json"))
# 任务体系：优先 Builder 构建的 tasks.json，回退种子 tasks_seed.json
TASK_TAXONOMY = _taxonomy_base("tasks", os.path.join("Tasks", "tasks.json"))
TASK_TAXONOMY_SEED = os.path.join(PROJECT_ROOT, "classify", "Tasks", "tasks_seed.json")
# 岗位体系：jobs_v2.json（v2.0，9 类别 131 岗位，2026-08-22 起为运行时基准；
# v1 jobs0806.json 255 岗位保留存档）
JOB_TAXONOMY = _taxonomy_base("jobs", os.path.join("Jobs", "jobs_v2.json"))

# ---------------- 缓存 ----------------
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
# 缓存文件名按 mode 区分：cache/{mode}.jsonl
CACHE_MODE_FILE = "cache_{mode}.jsonl"

# ---------------- 抽取参数（settings.yaml → jd_extract） ----------------
BATCH_SIZE = _settings("jd_extract", "batch_size", default=15)
SENTENCE_MIN_LEN = _settings("jd_extract", "sentence_min_len", default=4)
SENTENCE_MAX_LEN = _settings("jd_extract", "sentence_max_len", default=200)
# 计数语义：sentence 按句子出现次数计（每句贡献 1）；occurrence 按提及次数计
COUNT_MODE = _settings("jd_extract", "count_mode", default="sentence")

# ---------------- LLM 输出上限（settings.yaml → llm） ----------------
MAX_TOKENS = _settings("llm", "batch_max_tokens", default=8000)
BATCH_MAX_TOKENS = _settings("llm", "batch_max_tokens", default=8000)
MAX_TOKENS_CAP = _settings("llm", "max_tokens_cap", default=32000)
TIMEOUT = _settings("llm", "timeout", default=180)
RETRIES = _settings("llm", "retries", default=3)
CONCURRENCY = _settings("llm", "concurrency", default=20)   # 单 key 批次并发量（提速；1=串行）
# 并行 api-key 数（多 key 请求级轮转；总批次并发 = CONCURRENCY × 实际启用数）
API_KEYS_PARALLEL = int(_settings("llm", "api_keys_parallel", default=1) or 1)
# key 预检：KeyRing 首次构建时并行探测启用 key，不可用者本进程内剔除（false=跳过）
KEY_PROBE = bool(_settings("llm", "key_probe", default=True))


def concurrency_total():
    """总批次并发 = llm.concurrency × 启用 key 数（每 key 并发不超 CONCURRENCY，
    多 key 轮转分摊限速；单 key 行为与旧版一致）。运行时求值（key 文件可增删）。"""
    return CONCURRENCY * max(1, len(active_api_keys()))

# ---------------- 行业新闻（ΔG 分类） ----------------
# 过滤/提取参数（导语窗口 / 正文截断）统一在 codes/settings.yaml → news 节，
# 由 news_filter.py / news_extractor.py 按路径直接读取（不依赖本文件，跨模块导入安全）。


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
    """并行启用集：全部 key 的前 llm.api_keys_parallel 个（key 文件不足时以实际数为准）。"""
    keys = load_api_keys()
    return keys[:max(1, API_KEYS_PARALLEL)] if keys else []


def load_api_key():
    """读取 DeepSeek API key：--api-key > codes/api-key.txt > 环境变量（首个，兼容单 key 调用方）。"""
    return (load_api_keys() or [""])[0]
