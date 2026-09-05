# -*- coding: utf-8 -*-
"""JD → 岗位体系 v2 归类引擎（2026-08-21）。

四路信号：
  1a. 岗位名层（tier1，零 LLM）：岗位 name_zh 出现在标题 = 最强信号
      （build_name_matchers；缓解跨岗共享关键词的首现排序抢占）
  1b. 词库快路（tier1/tier2，零 LLM）：jobs_v2 keywords 命中即归类
      （复用 common.rule_stacks / StackMatchers，与技术栈引擎同一匹配语义）
  2.  LLM 兜底（tier3）：词库未命中且标题非排除域 → batch=20 送 deepseek-v4-flash
      多标签归类（1-2 岗；2026-08-22 起非 IT 域输出显式 `["非IT相关"]` 单标签，
      落缓存为 jobs=[] + non_it=True，与规则排除表同口径——内容级非IT过滤信号，
      向量阶段 load_non_it_keys() 据此跳过）→
      output/jd_job_cache.jsonl（只存 LLM 判定，词库命中由引擎在线重算；
      指纹 common.jd_text_key 同文只判一次）
  3.   向量比对（观测信号，独立于 1-2，当前不参与归类决策）：
       - 任务向量：JD 的 0/1 任务向量 vs 基图岗位任务向量（data/graph/{窗口}/base/
         job_task.json 边按 v1 code → v2 code 聚合，excluded 岗位边自然丢弃）
       - 技能向量：同上（job_skill.json）
       逐 JD 计算余弦相似度 top-k。约定用途（与设计方案一致）：验证有效后与 LLM 判定
       结合——向量差距大时转向新岗位探索，同时可观察岗位任务/技能模式漂移。
       已知噪声源（观察自 job_2026_1_1 验证）：基图岗位向量来自小规模抽样构建
       （200 JD → 51/131 岗有向量），且 LLM 提案关键词存在跨岗泛词
       （一致性调优 = 关键词精简 + 全量基图重建，均为后续工作）。

排除表（JOB_EXCLUDE_TITLE_WORDS）：物理制造/职能域照收 common.EXCLUDE_TITLE_WORDS，
但剔除 v2 已收录的产品/项目管理岗（产品经理 PD-01 / 产品专员→PD-01 / 项目经理 MGT-05）。

用法：
  python classify_job.py --stats [--files x.csv]           # 零 LLM：词库命中率/排除量预估
  python classify_job.py --dry-run --files x.csv --limit N # 预览送 LLM 条数与样例
  python classify_job.py --files x.csv --limit N           # 测试范围全流程（含 LLM 兜底）
  python classify_job.py                                   # 全量（断点续跑）
  python classify_job.py --vectors --files x.csv --limit N # 构建 JD 任务/技能 0/1 向量缓存
  python classify_job.py --vector-report --files x.csv     # 向量比对报告：top-k 相似岗位
                                                           #   + 与词库/LLM 归类的一致率
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import defaultdict

import common
from build_jobs import call_llm_raw

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS_V2_PATH = os.path.join(common.REPO, "classify", "Jobs", "jobs_v2.json")
TASKS_PATH = os.path.join(common.REPO, "classify", "Tasks", "tasks.json")
SKILLS_PATH = os.path.join(common.REPO, "classify", "Skills", "skills0821.json")
GRAPH_ROOT = os.path.join(common.REPO, "data", "graph")

JOB_PROGRESS = os.path.join(common.OUT_DIR, "jd_job_progress.jsonl")
JD_JOB_CACHE = os.path.join(common.OUT_DIR, "jd_job_cache.jsonl")
VEC_PROGRESS = os.path.join(common.OUT_DIR, "jd_vec_progress.jsonl")
JD_VEC_CACHE = os.path.join(common.OUT_DIR, "jd_vec_cache.jsonl")

BATCH = 20          # 岗位归类：每次 LLM 调用的 JD 条数
VEC_BATCH = 10      # 任务/技能向量标注：每次调用条数（标签表更长）
BODY_CHARS = 600
MAX_TOKENS = 4000
TOPK = 3            # 向量比对默认 top-k

# 排除表：v2 已把产品/项目管理岗收录为实体，从技术栈排除表中移出；
# 项目专员/文员等 v2 未收录的职能域照收（词库未命中时直接空岗不送 LLM）
JOB_EXCLUDE_TITLE_WORDS = [w for w in common.EXCLUDE_TITLE_WORDS
                           if w not in ("产品经理", "产品专员", "项目经理")]


def is_excluded_job_title(title):
    return any(w in (title or "") for w in JOB_EXCLUDE_TITLE_WORDS)


# ---------------- 体系加载 ----------------

def load_jobs_v2():
    """→ (detail: code→节点, categories: [..])。v2 引擎固定读 jobs_v2.json
    （taxonomy_base.json 的 jobs 开关仍指 v1 jobs0806，待消费者统一切换时处理）。"""
    with open(JOBS_V2_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["detail"], data["categories"]


def load_task_skill_labels():
    """任务（35）/技能（49）标签清单 → (tasks, skills)：[{code, name_zh}]。"""
    with open(TASKS_PATH, encoding="utf-8") as f:
        tasks = [{"code": t["code"], "name_zh": t["name_zh"]}
                 for t in json.load(f)["tasks"]]
    with open(SKILLS_PATH, encoding="utf-8") as f:
        skills = [{"code": d["code"], "name_zh": d["name_zh"]}
                  for d in json.load(f)["detail"].values()]
    return tasks, skills


def job_label_text(detail, categories):
    """prompt 中的岗位清单：按大类分组的 `CODE: 名称（关键词提示）` 行。"""
    by_cat = defaultdict(list)
    for code, d in sorted(detail.items()):
        hint = "、".join((d.get("keywords") or [])[:4])
        by_cat[d["category"]].append(f"{code}: {d['name_zh']}（{hint}）" if hint else f"{code}: {d['name_zh']}")
    lines = []
    for c in categories:
        lines.append(f"{c['code']} {c['name_zh']}：")
        lines.extend("  " + x for x in by_cat[c["code"]])
    return "\n".join(lines)


# ---------------- 主流程：词库快路 + LLM 兜底 ----------------

JOB_PROMPT = """你是招聘JD岗位归类器。给定信息技术岗位体系（9 大类 {n_jobs} 岗位，名称后括号内为识别关键词提示），判断每条 JD 最可能对应的岗位。
岗位体系：
{job_list}

规则：
- 每条 JD 归入 1-2 个岗位（按可能性排序，jobs 为岗位 code 数组）；仅当标题与正文都无法对应任何信息技术岗位、又看不出明确非 IT 域时才输出空数组
- 体系只覆盖信息技术域：电气/电力、机械/制造/工艺、质量检验、建筑/施工、行政/文员、销售/客服、绘图/美工、维修等非 IT 域 JD **必须输出 `["非IT相关"]` 固定单标签**（confidence 填判断把握），不要勉强匹配相近 IT 岗位
- **泛词岗必须以正文技术内容定域**（2026-09-03）：标题为项目经理/项目助理/产品经理/产品工程师/总监/主管/运维/技术支持/售后/售前/测试工程师/系统工程师等泛词时，正文没有软件/数据/AI/网络/信息安全/云等技术实质内容（仅有行业/制造/设备/涂料/医疗/商务/进度管理描述）→ 输出 `["非IT相关"]`，**不得按标题字面匹配 IT 岗位**（如制造行业项目经理≠MGT-05、风电电力运维≠OPS-01、声学/射频/生产测试≠QA-01、非软件产品经理≠PD-01）
- 优先依据标题岗位名，其次正文职责与技术栈；初级/高级/资深等级别词不影响岗位判断；标题与正文矛盾时以正文实际职责为准
严格只输出一个 JSON 数组，不要任何其他文字，格式：
[{{"id":1,"jobs":["XX-01"],"confidence":0到1}}, ...]
待分类 JD（id. 标题 | 正文摘录）：
{items}}"""


def iter_jd_rows(files, limit=None):
    """复用 classify_stacks 的行迭代（--files 逗号分隔，相对 data/jd_dataset）。"""
    import classify_stacks
    return classify_stacks.iter_jd_rows(files, limit)


def build_name_matchers(detail):
    """岗位名匹配器（tier1a）：名称出现在标题 = 最强信号（与技术栈引擎的
    "优先依据标题岗位名" 同原则，缓解跨岗共享关键词的排序抢占，如 "Java"
    同时是数据开发岗关键词，但标题「Java开发工程师」应命中岗位名）。"""
    return common.StackMatchers({c: {"keywords": [d["name_zh"]]} for c, d in detail.items()})


# 泛词岗位名（2026-09-03，评测实测驱动）：这些名称同时是大量非 IT 岗的常见头衔
# （制造/医疗/涂料行业项目经理、风电电力运维、声学/射频/生产测试、非软件产品经理等），
# 标题命中不可直接采信——须送 LLM 按正文技术内容定域（JOB_PROMPT 泛词岗规则）。
# 2026-05 评测集实证：泛词名直收使 it_related 在泛词岗上 ~30% 错判（36/121 样本）。
AMBIGUOUS_JOB_NAMES = {
    "项目经理", "产品经理", "电商产品经理", "测试工程师", "运维工程师",
    "系统工程师", "技术支持工程师", "技术经理", "技术总监", "解决方案经理",
    "产品工程师", "软件工程师",
}


def _load_presample_keys(csv_path):
    """Stage S0 预抽样键集（{窗口}.presample.json，graph/jd_pre_sample.py 产）。

    keys 非空（触发）→ 返回已选键 set；未触发/无文件 → None（不过滤）。
    路径约定与 jobcls 同目录：data/timeline/jd_derived/{窗口}.presample.json。
    """
    derived = os.path.join(common.REPO, "data", "timeline", "jd_derived")
    p = os.path.join(derived, os.path.splitext(os.path.basename(csv_path))[0] + ".presample.json")
    if not os.path.exists(p):
        return None
    try:
        rec = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    keys = rec.get("keys")
    return set(keys) if keys else None


def collect(files, limit=None, strict=False, presample=None):
    """单遍扫描 → 名称层 + 词库快路 + 排除表 → (misses, stats, classified)。

    classified: key → {"jobs": [code...], "tier": 1/2}（词库命中，引擎侧在线重算的同源结果）
    tier 1 = 标题命中（岗位名 或 体系关键词）；tier 2 = 正文关键词。
    misses: key → {title, body}（待 LLM 判定）。

    strict=True（严格门，基图管线口径）：仅「岗位名出现在标题」的 tier1 直接采信；
    关键词命中（tier1 关键词 / tier2 正文）泛词误报多（'测试'→通信测试、'监控'→运维、
    '质检'→数据标注），一律改送 LLM 按内容复核；排除表行为不变。

    presample（Stage S0，2026-09-03）：大窗预抽样键集，非 None 时未选键在指纹计算
    后跳过（规则与 LLM 都不见）→ jobcls 只含已选键，S/D0/B/v2 全链自动受限。
    """
    detail, _ = load_jobs_v2()
    name_m = build_name_matchers(detail)
    matchers = common.StackMatchers(detail)
    misses, seen, classified = {}, set(), {}
    stats = {"rows": 0, "unique": 0, "unique_all": 0, "presampled_out": 0,
             "tier1": 0, "tier1_name": 0, "tier2": 0,
             "excluded": 0, "miss": 0, "strict": strict}
    for _, title, text in iter_jd_rows(files, limit):
        stats["rows"] += 1
        key = common.jd_text_key(title, text)
        if key in seen:
            continue
        seen.add(key)
        stats["unique_all"] += 1
        if presample is not None and key not in presample:
            stats["presampled_out"] += 1
            continue
        stats["unique"] += 1
        name_hits = [c for _, c in name_m.scan(title)]
        if name_hits:
            # 泛词岗位名（项目经理/运维/测试等）不可凭标题直收——全部命中均为泛词时
            # 改送 LLM 按正文定域；存在非泛词命中（如「Java开发工程师」）仍直收
            direct = [c for c in name_hits
                      if detail[c]["name_zh"] not in AMBIGUOUS_JOB_NAMES]
            if direct:
                stats["tier1"] += 1
                stats["tier1_name"] += 1
                classified[key] = {"jobs": direct[:2], "tier": 1}
                continue
            stats["ambig_name_to_llm"] = stats.get("ambig_name_to_llm", 0) + 1
        jobs, tier = common.rule_stacks(matchers, title, text, cap=2)
        if tier == 1:
            stats["tier1"] += 1
            if not strict:
                classified[key] = {"jobs": jobs, "tier": 1}
                continue
        elif tier == 2:
            stats["tier2"] += 1
            if not strict:
                classified[key] = {"jobs": jobs, "tier": 2}
                continue
        if is_excluded_job_title(title):
            stats["excluded"] += 1
            # 规则级非IT（排除表）：记录为 non_it（tier 0），与 LLM 显式判定同口径
            classified[key] = {"jobs": [], "tier": 0, "non_it": True}
        else:
            stats["miss"] += 1
            misses[key] = {"title": title, "body": text[:BODY_CHARS]}
    return misses, stats, classified


def load_progress(path, valid_keys):
    done = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    for k, v in json.loads(line).items():
                        if k in valid_keys:
                            done[k] = v
    return done


# ---------------- 窗口级归类缓存（{窗口}.jobcls.json：A 门跑满后 S/B 免重复扫描） ----------------
# 大窗（60 万行）collect 扫描 ~33 分钟；A/S/B 各扫一遍纯浪费。A 跑满后把"规则层+LLM 缓存"
# 的合并结果落盘，S（jd_sample）与 B（run_jd_extract）直接读。存 PRE-scope 原始形态
# （jobs/tier/non_it），it_scope 过滤仍由管线侧在线应用——范围调整无需重建缓存。

def _jobcls_path(csv_path):
    """窗口归类缓存路径：data/timeline/jd_derived/{窗口}.jobcls.json（与源 CSV 分离）。"""
    derived = os.path.join(common.REPO, "data", "timeline", "jd_derived")
    return os.path.join(derived, os.path.splitext(os.path.basename(csv_path))[0] + ".jobcls.json")


def _jobs_v2_sha():
    h = hashlib.sha256()
    with open(JOBS_V2_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_jobcls_cache(csv_path, cls_raw, st, strict):
    """写窗口归类缓存（调用方保证 A 已跑满：无未分类残留）。"""
    rec = {
        "schema_version": "0.1",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "csv": os.path.basename(csv_path),
        "csv_mtime": os.path.getmtime(csv_path), "csv_size": os.path.getsize(csv_path),
        "jobs_v2_sha256": _jobs_v2_sha(), "strict": bool(strict),
        "stats": st,
        "classification": cls_raw,   # {jd_key: {jobs, tier, non_it}}（PRE-scope）
    }
    os.makedirs(os.path.dirname(_jobcls_path(csv_path)), exist_ok=True)
    with open(_jobcls_path(csv_path), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    print(f"[jobcls] 窗口归类缓存已写：{_jobcls_path(csv_path)}（{len(cls_raw)} 条）", flush=True)


def read_jobcls_cache(csv_path, strict):
    """读缓存 → (cls_raw, st)；不新鲜（csv/体系/口径变更）或不存在 → (None, None)。"""
    p = _jobcls_path(csv_path)
    if not os.path.exists(p):
        return None, None
    try:
        rec = json.load(open(p, encoding="utf-8"))
        if (rec.get("csv_mtime") == os.path.getmtime(csv_path)
                and rec.get("csv_size") == os.path.getsize(csv_path)
                and rec.get("jobs_v2_sha256") == _jobs_v2_sha()
                and rec.get("strict") == bool(strict)):
            return rec["classification"], rec["stats"]
    except (OSError, ValueError, KeyError):
        pass
    return None, None


def merged_classification(files, limit=None, strict=False, presample=None):
    """collect 规则层 + jd_job_cache LLM 判定 → (cls_raw, st)。

    cls_raw: {jd_key: {"jobs": [code], "tier": 1/2/3/0, "non_it": bool, "unclassified": ...}}，
    PRE-it_scope（岗位范围过滤属管线口径，由 graph 侧消费时在线应用）。
    供 A 跑满后的 S/B 复用（配套 {窗口}.jobcls.json 缓存）。
    presample（Stage S0）：透传 collect，未选键不进 cls_raw（缓存与工作宇宙一致）。
    """
    misses, st, classified = collect(files, limit, strict=strict, presample=presample)
    llm = {}
    if os.path.exists(JD_JOB_CACHE):
        with open(JD_JOB_CACHE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    v = json.loads(line)
                    if v.get("key"):
                        llm[v["key"]] = v
    cls_raw = {}
    for key, cls in classified.items():
        cls_raw[key] = {"jobs": cls.get("jobs") or [], "tier": cls.get("tier"),
                        "non_it": bool(cls.get("non_it", False))}
    for key in misses:
        v = llm.get(key)
        if v:
            cls_raw[key] = {"jobs": [c for c in (v.get("jobs") or [])],
                            "tier": 3, "non_it": bool(v.get("non_it", False))}
        else:
            cls_raw[key] = {"jobs": [], "tier": None, "non_it": False, "unclassified": True}
    return cls_raw, st


def parse_array(text):
    """从 LLM 原文中提取 JSON 数组（容忍 ```json 围栏与前后杂讯）。"""
    text = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"响应中未找到 JSON 数组: {text[:120]!r}")
    return json.loads(text[start:end + 1])


_KEY_RING = None


def _key_ring():
    """jd_annotate 侧多 key 轮转（与 builder/llm.KeyRing 同语义的轻量自持实现）：
    api-key.txt 全量 key 的前 api_keys_parallel 个，round-robin、线程安全。
    首次构建时按 llm.key_probe 预检（settings 同开关）：不可用 key 本进程内剔除
    并打警告，减少批量归类中途"请求失败-换 key"的环节；全部不可用则中止。"""
    global _KEY_RING
    if _KEY_RING is None:
        import re
        import threading
        keys = []
        kf = os.path.join(common.REPO, "codes", "api-key.txt")
        if os.path.exists(kf):
            with open(kf, encoding="utf-8") as f:
                keys.extend(re.findall(r"sk-[A-Za-z0-9]+", f.read()))
        n = 1
        probe = True
        try:
            import yaml
            with open(os.path.join(common.REPO, "codes", "settings.yaml"), encoding="utf-8") as f:
                node = (yaml.safe_load(f) or {}).get("llm", {})
            n = int(node.get("api_keys_parallel") or 1)
            probe = bool(node.get("key_probe", True))
        except Exception:
            pass
        keys = list(dict.fromkeys(keys))[:max(1, n)]
        if probe and len(keys) > 1:
            from concurrent.futures import ThreadPoolExecutor

            def _probe(k):
                try:
                    call_llm_raw(k, "deepseek-v4-flash", '只输出 JSON：{"ok": true}',
                                 max_tokens=200, timeout=30, retries=1)
                    return True, ""
                except Exception as e:
                    return False, str(e)[:60]

            with ThreadPoolExecutor(max_workers=len(keys)) as ex:
                results = list(ex.map(_probe, keys))
            dead = [(k, e) for k, (ok, e) in zip(keys, results) if not ok]
            keys = [k for k, (ok, _) in zip(keys, results) if ok]
            for k, e in dead:
                print(f"[classify] 预检剔除 key ...{k[-4:]}（{e}；本进程不使用，重启后重测）",
                      flush=True)
            if not keys:
                detail = "；".join(f"...{k[-4:]}: {e}" for k, e in dead)
                raise RuntimeError(f"预检失败：全部启用 key 不可用（{detail}）")

        class _Ring:
            def __init__(self, ks):
                self.ks, self.i, self.lock = ks, 0, threading.Lock()

            def next(self):
                with self.lock:
                    k = self.ks[self.i % len(self.ks)]
                    self.i += 1
                    return k

        _KEY_RING = _Ring(keys or [""])
    return _KEY_RING


def _llm_settings_concurrency():
    """settings.yaml → llm.concurrency × 启用 key 数（api_keys_parallel；缺省 8×1）。

    多 key 请求级轮转下总并发放大为每 key 并发 × key 数（每 key 限速压力与单 key
    版一致）；key 文件实际数量少于开关值时以文件为准。"""
    try:
        import yaml
        p = os.path.join(common.REPO, "codes", "settings.yaml")
        with open(p, encoding="utf-8") as f:
            node = yaml.safe_load(f) or {}
        base = int(node["llm"]["concurrency"])
    except (OSError, ValueError, KeyError, TypeError):
        base = 8
    n_keys = 1
    try:
        parallel = int(node["llm"].get("api_keys_parallel") or 1)
        import re
        with open(os.path.join(common.REPO, "codes", "api-key.txt"), encoding="utf-8") as f:
            found = len(set(re.findall(r"sk-[A-Za-z0-9]+", f.read())))
        n_keys = max(1, min(parallel, found))
    except Exception:
        pass
    return base * n_keys


def _llm_batch(api_key, model, misses, chunk, job_list):
    """单批（≤BATCH 条）LLM 归类 → result dict。整批失败对半重试（递归，线程内自洽，不碰共享态）。

    api_key=None → 每批经 _key_ring() 轮转取 key（多 key 分摊限速；对半重试递归
    时再次取环上下一个）；显式传 key 则固定（测试路径）。"""
    items = "\n".join(
        f"{j+1}. {misses[k]['title'] or '(无标题)'} | {misses[k]['body']}"
        for j, k in enumerate(chunk))
    detail, categories = load_jobs_v2()
    valid_codes = set(detail)
    # 清单岗位数与 job_label_text 渲染同口径（category 合法的才进 prompt；GJ- 转正
    # 归类后自动入列，缓存 key=JD 文本指纹不含 prompt 版本，历史缓存不回算）
    cat_codes = {c["code"] for c in categories}
    n_jobs = sum(1 for d in detail.values() if d.get("category") in cat_codes)
    prompt = (JOB_PROMPT.replace("{n_jobs}", str(n_jobs))
              .replace("{job_list}", job_list).replace("{items}", items))
    key = api_key or _key_ring().next()
    try:
        entries = parse_array(call_llm_raw(key, model, prompt, max_tokens=MAX_TOKENS))
        by_id = {int(e.get("id", 0)): e for e in entries if isinstance(e, dict)}
        result = {}
        for j, k in enumerate(chunk, 1):
            e = by_id.get(j) or {}
            raw_jobs = e.get("jobs") or []
            # 显式非IT标签：LLM 判为非 IT 域 → jobs 置空 + non_it=True（区别于"无法判断"）
            non_it = ("非IT相关" in raw_jobs) or bool(e.get("non_it"))
            jobs = [c for c in raw_jobs if c in valid_codes]
            result[k] = {"key": k, "title": misses[k]["title"],
                         "jobs": jobs[:2],
                         "confidence": float(e.get("confidence", 0.5)),
                         "source": "llm", "non_it": non_it}
        return result
    except Exception as e:
        if len(chunk) > 1:  # 整批失败 → 对半重试
            time.sleep(2)
            out = {}
            for half in (chunk[:1], chunk[1:]):
                out.update(_llm_batch(api_key, model, misses, half, job_list))
            return out
        print(f"  [fail] 单条 {chunk}: {e}", flush=True)
        return {k: {"key": k, "title": misses[k]["title"], "jobs": [],
                    "confidence": 0.0, "source": "llm", "non_it": False,
                    "error": str(e)[:60]} for k in chunk}


def llm_classify_jobs(api_key, model, misses, job_list, done, max_batches=0):
    """词库未命中且不在断点中的 JD → 分批 LLM 归类（批间并发，settings llm.concurrency）。

    结果/进度在主线程顺序合并与落盘（线程内只做 _post+解析，无共享态写入）。
    """
    from concurrent.futures import ThreadPoolExecutor
    pending = [k for k in misses if k not in done]
    if not pending:
        return done
    batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
    if max_batches:
        batches = batches[:max_batches]
        print(f"已达 --max-batches {max_batches}：本次只跑前 {len(batches)} 批（其余留断点续跑）", flush=True)
    concurrency = max(1, _llm_settings_concurrency())
    print(f"LLM 待处理 {len(pending)} 条（batch={BATCH}，批间并发 {concurrency}）...", flush=True)
    n_done = 0

    def _one(chunk):
        return _llm_batch(None, model, misses, chunk, job_list)   # None → 批级 key 轮转

    if concurrency > 1 and len(batches) > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            for result in ex.map(_one, batches):
                done.update(result)
                with open(JOB_PROGRESS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                n_done += 1
                if n_done % 10 == 0 or n_done == len(batches):
                    print(f"  进度 {min(n_done * BATCH, len(pending))}/{len(pending)}", flush=True)
    else:
        for chunk in batches:
            result = _one(chunk)
            done.update(result)
            with open(JOB_PROGRESS, "a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
            n_done += 1
            print(f"  进度 {min(n_done * BATCH, len(pending))}/{len(pending)}", flush=True)
    return done


def write_job_cache(done):
    """合并写出：保留既有缓存条目（跨运行累积，指纹键去重），本次判定覆盖同键旧值。

    跨月运行时（2025-10 → 11 → 12）各月 LLM 判定按指纹键并入同一缓存，
    互不覆盖；同文 JD 跨月复现只判一次。
    """
    os.makedirs(common.OUT_DIR, exist_ok=True)
    existing = {}
    if os.path.exists(JD_JOB_CACHE):
        with open(JD_JOB_CACHE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    v = json.loads(line)
                    if v.get("key"):
                        existing[v["key"]] = v
    existing.update(done)
    with open(JD_JOB_CACHE, "w", encoding="utf-8") as f:
        for k, v in sorted(existing.items()):
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    n = sum(1 for v in existing.values() if v.get("jobs"))
    n_nonit = sum(1 for v in existing.values() if v.get("non_it"))
    print(f"\n缓存已写出：{JD_JOB_CACHE}（累积 {len(existing)} 条）")
    print(f"LLM 判定累积 {len(existing)} 条（有岗 {n} / 非IT {n_nonit} / 未定 {len(existing) - n - n_nonit}）")


# ---------------- 向量信号：JD 0/1 标注 + 岗位向量比对 ----------------

VEC_PROMPT = """你是招聘JD任务/技能标注器。给定任务体系（{n_tasks} 项）与技能体系（{n_skills} 项），判断每条 JD 明确涉及哪些任务与技能。
任务体系：
{task_list}

技能体系：
{skill_list}

规则：
- 多标签 0/1 判断：JD 明确体现该任务/技能才选入，宁缺勿滥；与岗位无关的不选
- 依据标题与正文摘录中的职责、技术栈与工具线索
严格只输出一个 JSON 数组，不要任何其他文字，格式：
[{{"id":1,"tasks":["T-01"],"skills":["F-1-01"]}}, ...]
待标注 JD（id. 标题 | 正文摘录）：
{items}}"""


def load_non_it_keys():
    """从岗位归类缓存读 LLM 判定的非IT指纹集合（内容级过滤信号，供下游复用）。"""
    out = set()
    if os.path.exists(JD_JOB_CACHE):
        with open(JD_JOB_CACHE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    v = json.loads(line)
                    if v.get("non_it"):
                        out.add(v.get("key"))
    return out


def build_vectors(api_key, model, files, limit, max_batches=0):
    """扫描 → 指纹去重 → 排除域/归类非IT跳过 → LLM 0/1 任务+技能标注 → jd_vec_cache.jsonl。"""
    detail, _ = load_jobs_v2()
    tasks, skills = load_task_skill_labels()
    valid_t = {t["code"] for t in tasks}
    valid_s = {s["code"] for s in skills}
    non_it_keys = load_non_it_keys()
    misses, seen = {}, set()
    for _, title, text in iter_jd_rows(files, limit):
        key = common.jd_text_key(title, text)
        if key in seen:
            continue
        seen.add(key)
        if is_excluded_job_title(title) or key in non_it_keys:
            continue  # 非IT域（规则排除表或归类缓存 LLM 判定）：向量必然近空，不送 LLM
        misses[key] = {"title": title, "body": text[:BODY_CHARS]}
    done = load_progress(VEC_PROGRESS, set(misses))
    pending = [k for k in misses if k not in done]
    print(f"向量标注：范围内 unique {len(seen)}，待标注 {len(pending)}（batch={VEC_BATCH}）", flush=True)
    task_list = "\n".join(f"{t['code']}: {t['name_zh']}" for t in tasks)
    skill_list = "\n".join(f"{s['code']}: {s['name_zh']}" for s in skills)
    n_batches = 0
    for i in range(0, len(pending), VEC_BATCH):
        if max_batches and n_batches >= max_batches:
            print(f"已达 --max-batches，停止（剩余留断点续跑）", flush=True)
            break
        chunk = pending[i:i + VEC_BATCH]
        items = "\n".join(
            f"{j+1}. {misses[k]['title'] or '(无标题)'} | {misses[k]['body']}"
            for j, k in enumerate(chunk))
        prompt = (VEC_PROMPT.replace("{n_tasks}", str(len(tasks))).replace("{n_skills}", str(len(skills)))
                  .replace("{task_list}", task_list).replace("{skill_list}", skill_list)
                  .replace("{items}", items))
        result = {}
        try:
            entries = parse_array(call_llm_raw(api_key, model, prompt, max_tokens=MAX_TOKENS))
            by_id = {int(e.get("id", 0)): e for e in entries if isinstance(e, dict)}
            for j, k in enumerate(chunk, 1):
                e = by_id.get(j) or {}
                result[k] = {"key": k, "title": misses[k]["title"],
                             "tasks": [c for c in (e.get("tasks") or []) if c in valid_t],
                             "skills": [c for c in (e.get("skills") or []) if c in valid_s],
                             "source": "llm"}
        except Exception as e:
            if len(chunk) > 1:  # 整批失败 → 对半重试
                time.sleep(2)
                for half in (chunk[:1], chunk[1:]):
                    _vec_retry(api_key, model, {k: misses[k] for k in half},
                               task_list, skill_list, done)
                continue
            print(f"  [fail] 单条: {e}", flush=True)
            for k in chunk:
                result[k] = {"key": k, "title": misses[k]["title"], "tasks": [],
                             "skills": [], "source": "llm", "error": str(e)[:60]}
        done.update(result)
        n_batches += 1
        with open(VEC_PROGRESS, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(f"  进度 {min(i + VEC_BATCH, len(pending))}/{len(pending)}", flush=True)
    with open(JD_VEC_CACHE, "w", encoding="utf-8") as f:
        for k, v in sorted(done.items()):
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"\n向量缓存已写出：{JD_VEC_CACHE}（{len(done)} 条）")


def _vec_retry(api_key, model, sub, task_list, skill_list, done):
    """整批失败的极小重试（单条，简化处理）。"""
    k = next(iter(sub))
    items = f"1. {sub[k]['title'] or '(无标题)'} | {sub[k]['body']}"
    prompt = (VEC_PROMPT.replace("{n_tasks}", "35").replace("{n_skills}", "49")
              .replace("{task_list}", task_list).replace("{skill_list}", skill_list)
              .replace("{items}", items))
    try:
        entries = parse_array(call_llm_raw(api_key, model, prompt, max_tokens=MAX_TOKENS))
        e = entries[0] if entries else {}
        done[k] = {"key": k, "title": sub[k]["title"],
                   "tasks": list(e.get("tasks") or []), "skills": list(e.get("skills") or []),
                   "source": "llm"}
        with open(VEC_PROGRESS, "a", encoding="utf-8") as f:
            f.write(json.dumps(done[k], ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [fail] 重试失败: {e}", flush=True)


def load_job_vectors(window=None):
    """基图岗位向量 → (task_vec, skill_vec, info)。

    data/graph/{窗口}/base/job_task|job_skill.json 的边（src=v1 岗位码）按
    jobs_v2.source_codes 聚合到 v2 岗位（多条 v1 合并求和；未映射的 v1 边丢弃）。
    """
    if not window:
        wins = sorted(d for d in os.listdir(GRAPH_ROOT)
                      if os.path.isdir(os.path.join(GRAPH_ROOT, d)))
        if not wins:
            sys.exit(f"[ERR] 无基图快照：{GRAPH_ROOT} 为空（先运行 graph/run_base_build.py）")
        window = wins[-1]
    base = os.path.join(GRAPH_ROOT, window, "base")
    detail, _ = load_jobs_v2()
    v1_to_v2 = {}
    for code, d in detail.items():
        for src in d.get("source_codes") or []:
            v1_to_v2[src] = code

    def aggregate(filename):
        vec, n_edge, n_drop = defaultdict(dict), 0, 0
        with open(os.path.join(base, filename), encoding="utf-8") as f:
            for e in json.load(f)["edges"]:
                n_edge += 1
                v2 = v1_to_v2.get(e["src"])
                if not v2:
                    n_drop += 1
                    continue
                vec[v2][e["dst"]] = vec[v2].get(e["dst"], 0.0) + float(e["weight"])
        return dict(vec), n_edge, n_drop

    task_vec, te, td = aggregate("job_task.json")
    skill_vec, se, sd = aggregate("job_skill.json")
    info = {"window": window, "job_task_edges": te, "job_skill_edges": se,
            "dropped_v1_edges": td + sd,
            "n_v2_jobs_task": len(task_vec), "n_v2_jobs_skill": len(skill_vec)}
    return task_vec, skill_vec, info


def cosine(jd_codes, job_vec):
    """JD 0/1 向量（code 集合）与岗位权重向量的余弦相似度。"""
    if not jd_codes or not job_vec:
        return 0.0
    dot = sum(job_vec.get(c, 0.0) for c in jd_codes)
    if dot <= 0:
        return 0.0
    na = math.sqrt(len(jd_codes))
    nb = math.sqrt(sum(v * v for v in job_vec.values()))
    return dot / (na * nb)


def vector_report(files, limit, topk=TOPK, show=8):
    """向量比对报告：JD 向量 vs 岗位向量 top-k + 与词库/LLM 归类的一致率。"""
    detail, _ = load_jobs_v2()
    task_vec, skill_vec, info = load_job_vectors()
    print(f"岗位向量来源：data/graph/{info['window']}/base"
          f"（job_task {info['job_task_edges']} 边 → v2 {info['n_v2_jobs_task']} 岗；"
          f"job_skill {info['job_skill_edges']} 边 → v2 {info['n_v2_jobs_skill']} 岗；"
          f"丢弃 v1 未映射边 {info['dropped_v1_edges']}）")

    # 范围内 JD 的归类结果（词库在线重算 + LLM 缓存，不产生新调用）
    misses, st, classified = collect(files, limit)
    if os.path.exists(JD_JOB_CACHE):
        with open(JD_JOB_CACHE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    v = json.loads(line)
                    if v["jobs"]:
                        classified[v["key"]] = {"jobs": v["jobs"], "tier": 3}
    vecs = {}
    if os.path.exists(JD_VEC_CACHE):
        with open(JD_VEC_CACHE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    v = json.loads(line)
                    vecs[v["key"]] = v

    # 只统计当前扫描范围内的 JD
    keys = []
    seen = set()
    for _, title, text in iter_jd_rows(files, limit):
        key = common.jd_text_key(title, text)
        if key not in seen and key in vecs:
            seen.add(key)
            keys.append(key)
    if not keys:
        print(f"[WARN] 向量缓存与当前范围无交集（先运行 --vectors）")
        return

    stats = {"n": 0, "has_cls": 0, "agree_task": 0, "agree_skill": 0, "agree_any": 0,
             "top1_task": [], "top1_skill": [], "empty_vec": 0}
    samples = []
    for key in keys:
        v = vecs[key]
        t, s = v.get("tasks") or [], v.get("skills") or []
        if not t and not s:
            stats["empty_vec"] += 1
            continue
        sim_t = sorted(((cosine(t, tv), c) for c, tv in task_vec.items()), reverse=True)
        sim_s = sorted(((cosine(s, sv), c, ) for c, sv in skill_vec.items()), reverse=True)
        stats["n"] += 1
        stats["top1_task"].append(sim_t[0][0] if sim_t else 0.0)
        stats["top1_skill"].append(sim_s[0][0] if sim_s else 0.0)
        cls = classified.get(key)
        cls_codes = cls["jobs"][0:1] if cls else []
        if cls_codes:
            stats["has_cls"] += 1
            top_t = {c for _, c in sim_t[:1]}
            top_s = {c for _, c in sim_s[:1]}
            if cls_codes[0] in top_t:
                stats["agree_task"] += 1
            if cls_codes[0] in top_s:
                stats["agree_skill"] += 1
            if cls_codes[0] in (top_t | top_s):
                stats["agree_any"] += 1
        if len(samples) < show:
            samples.append((v["title"], cls, sim_t[:topk], sim_s[:topk]))

    n = max(stats["n"], 1)
    print(f"\n向量比对样本 {stats['n']} 条（空向量 {stats['empty_vec']}；其中已归类 {stats['has_cls']}）")
    if stats["has_cls"]:
        h = stats["has_cls"]
        print(f"与归类一致率（top-1）：任务空间 {stats['agree_task']}/{h}（{stats['agree_task']/h:.0%}）| "
              f"技能空间 {stats['agree_skill']}/{h}（{stats['agree_skill']/h:.0%}）| "
              f"任一命中 {stats['agree_any']}/{h}（{stats['agree_any']/h:.0%}）")
    print(f"top-1 相似度均值：任务 {sum(stats['top1_task'])/n:.3f} | 技能 {sum(stats['top1_skill'])/n:.3f}")
    print("\n样例（标题 | 归类 → 任务/技能 top 岗位）：")
    for title, cls, st_t, st_s in samples:
        cls_txt = "+".join(detail[c]["name_zh"] for c in cls["jobs"]) if cls else "(未归类)"
        t_txt = " ".join(f"{detail[c]['name_zh']}:{x:.2f}" for x, c in st_t)
        s_txt = " ".join(f"{detail[c]['name_zh']}:{x:.2f}" for x, c in st_s)
        print(f"  [{title or '(无标题)'}]\n    归类: {cls_txt}\n    任务: {t_txt}\n    技能: {s_txt}")


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="JD → 岗位体系 v2 归类（词库+LLM+向量比对）")
    ap.add_argument("--files", default="", help="逗号分隔文件名（相对 data/jd_dataset），默认全部")
    ap.add_argument("--limit", type=int, default=None, help="每文件最多处理行数（测试用）")
    ap.add_argument("--stats", action="store_true", help="零 LLM：词库命中率/排除量统计")
    ap.add_argument("--strict", action="store_true",
                    help="严格门：仅岗位名命中直收，关键词命中一律送 LLM 按内容复核（基图管线口径）")
    ap.add_argument("--dry-run", action="store_true", help="预览将送 LLM 的条数与样例")
    ap.add_argument("--max-batches", type=int, default=0, help="最多 LLM 调用批数（0=不限）")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    ap.add_argument("--vectors", action="store_true",
                    help="构建 JD 任务/技能 0/1 向量缓存（LLM，独立进度断点）")
    ap.add_argument("--vector-report", action="store_true",
                    help="向量比对报告：余弦 top-k 相似岗位 + 与归类结果一致率（不产生新归类调用）")
    args = ap.parse_args()

    if args.vectors:
        from build_jobs import common_cj
        api_key = common_cj().load_api_key("")
        if not api_key:
            print("错误：未找到 API key", file=sys.stderr)
            sys.exit(1)
        build_vectors(api_key, args.model, args.files, args.limit, args.max_batches)
        return
    if args.vector_report:
        vector_report(args.files, args.limit)
        return

    print("扫描 JD（岗位名层 + 词库快路 + 排除表判定）...", flush=True)
    files_list = [f.strip() for f in args.files.split(",") if f.strip()]
    presample = None
    if len(files_list) == 1 and os.path.exists(files_list[0]):
        presample = _load_presample_keys(files_list[0])
        if presample is not None:
            print(f"[S0] 预抽样生效：仅归类已选 {len(presample)} 键（其余跳过）", flush=True)
    misses, st, _ = collect(args.files, args.limit, strict=args.strict, presample=presample)
    rule_cov = (st["tier1"] + st["tier2"]) / max(st["unique"], 1) * 100
    print(f"行 {st['rows']} → 去重 {st['unique']}"
          + (f"（预抽样前 unique {st['unique_all']}，未选跳过 {st['presampled_out']}）"
             if presample is not None else "")
          + f" | 标题命中：岗位名 {st['tier1_name']} + 关键词 {st['tier1'] - st['tier1_name']}"
          + f" | 正文关键词 {st['tier2']}（合计 {st['tier1'] + st['tier2']}，{rule_cov:.1f}%）"
          + f" | 排除表判空 {st['excluded']} | 未命中待 LLM {st['miss']}")
    if st["miss"]:
        est = (st["miss"] + BATCH - 1) // BATCH
        print(f"预计 LLM 调用 {est} 次（batch={BATCH}）")
    if args.stats:
        return
    if args.dry_run:
        print("\n待送 LLM 样例（前 5 条）：")
        for k in list(misses)[:5]:
            print(f"  [{misses[k]['title'] or '(无标题)'}] {misses[k]['body'][:80]}…")
        return
    if not misses:
        print("全部词库命中，无需 LLM")
        return

    done = load_progress(JOB_PROGRESS, set(misses))
    if done:
        print(f"断点恢复：已完成 {len(done)} 条")
    if len(done) < len(misses):
        from build_jobs import common_cj
        api_key = common_cj().load_api_key("")
        if not api_key:
            print("错误：未找到 API key", file=sys.stderr)
            sys.exit(1)
        detail, categories = load_jobs_v2()
        job_list = job_label_text(detail, categories)
        done = llm_classify_jobs(api_key, args.model, misses, job_list, done, args.max_batches)
    write_job_cache(done)
    # 窗口级归类缓存：单文件且已跑满（无未分类残留）时落盘，S/B 阶段免重复扫描大 CSV
    if len(files_list) == 1 and os.path.exists(files_list[0]) \
            and not args.limit and not args.max_batches and len(done) >= len(misses):
        try:
            cls_raw, st_cache = merged_classification(files_list[0], None, strict=args.strict,
                                                      presample=presample)
            if not any(v.get("unclassified") for v in cls_raw.values()):
                write_jobcls_cache(files_list[0], cls_raw, st_cache, strict=args.strict)
        except Exception as e:
            print(f"[jobcls] 窗口缓存写入失败（不影响归类结果）：{e}", flush=True)


if __name__ == "__main__":
    main()
