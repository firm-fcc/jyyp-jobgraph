# -*- coding: utf-8 -*-
"""Stage B：全量 IT JD 抽取 → jd_vectors 源文件（0/1 向量 + 证据 + 技术栈/职级）。

读 data/timeline/jd/{窗口}.csv → 按 jd_text_key join Stage A 的岗位归类
（classify_job.collect 规则层在线重算 + jd_job_cache LLM 判定）→ 跳过 non_it
→ 对 IT JD 跑 Extractor(skill)+Extractor(task)（句级缓存 cache_skill/cache_task，
跨 JD 去重）→ 每条 JD 写一条记录到 data/timeline/jd_derived/{窗口}.jd_vectors.jsonl
+ .meta.json。

两版本向量：
- skill_vec_01：sorted present codes（0/1，供聚合，全 49 技能含聚合信号技能）
- task_vec_01：sorted present task codes
- skillpoint_map：{skill: [skillpoints]}
- evidence_map：{skill: [证据句]}（Stage C 熟练度输入，本阶段副产品零额外 LLM；
  6 聚合信号技能剔除——同 jd_proficiency._classify_evidence 口径）
- skill_vec_prof：{} 占位，Stage C 回填 P1-P4/U

副产品：统计唯一 (技能, 归一化证据) 对数 → 供 Stage C 熟练度调用估算与范围决定。

复用：base_builder.parse_salary_monthly（薪资解析）、jd_annotate.common/annotate_jd
（jd_text_key/StackMatchers/rule_stacks/resolve_level）、classify_job.collect
（规则层 + 排除表）、jd_job_cache（Stage A LLM 判定）。
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TIMELINE_JD_DIR = os.path.join(REPO, "data", "timeline", "jd")
JD_JOB_CACHE = os.path.join(REPO, "codes", "jd_annotate", "output", "jd_job_cache.jsonl")

_ANN_DIR = os.path.join(REPO, "codes", "jd_annotate")
_GRAPH_DIR = HERE
_EXT_DIR = os.path.join(REPO, "codes", "extractor")
for _d in (_ANN_DIR, _GRAPH_DIR, _EXT_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import common as ann_common          # jd_annotate.common（jd_text_key/StackMatchers/rule_stacks/load_taxonomy）
import annotate_jd                   # resolve_level（职级规则，零 LLM）
import classify_job                 # collect（规则层在线重算 + 排除表）
import graph_config as gconfig      # GB_SALARY_WEIGHT / GB_MIN_TEXT_CHARS / BASE_NODE_FILES
import base_builder                  # parse_salary_monthly
import config as builder_config      # builder 版（graph_config 已把 codes/builder 置 sys.path 并缓存同一对象）

# 聚合信号技能（不定级）：同 codes/extractor/jd_proficiency.AGGREGATE_SKILLS——JD 对软技能
# 几乎只写无梯度表述，熟练度定级无意义；evidence_map 剔除以与评估器同口径
_AGGREGATE_SKILLS = {"F-1-01", "F-1-03", "F-1-04", "F-3-04", "F-4-01", "F-4-02"}


def _window_end_date(window):
    """YYYY-MM → 窗末 date（叠层确证的 store now / 出生窗戳口径，同 jd_delta_v2）。"""
    import calendar
    y, m = int(window[:4]), int(window[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _recheck_overlay_pairs(ext, pairs, batch=25):
    """确证复核门：对 (记录, 实体项, 证据句) 批量独立复核，返回通过的下标集合。

    批间失败整批放弃（证据按 doc_id 幂等，下窗该 JD 再现时重试，无副作用）。
    """
    _EXT = _EXT_DIR
    if _EXT in sys.path:
        sys.path.remove(_EXT)
    sys.path.insert(0, _EXT)
    saved = sys.modules.pop("config", None)
    try:
        import prompts as ext_prompts
    finally:
        sys.path.remove(_EXT)
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)
    passed = set()
    for i in range(0, len(pairs), batch):
        chunk = pairs[i:i + batch]
        payload = json.dumps(
            [{"idx": j, "entity": it["name_zh"], "definition": (it.get("definition") or "")[:120],
              "sentence": sent[:200]}
             for j, (_rec, it, sent) in enumerate(chunk)], ensure_ascii=False)
        prompt = ext_prompts.PROMPT_OVERLAY_RECHECK.replace("{pairs}", payload)
        try:
            rows = ext.llm._post(prompt) or []
        except Exception as e:
            print(f"    [B] 确证复核批失败（本批留待下窗）：{e}", flush=True)
            continue
        for r in rows:
            if isinstance(r, dict) and r.get("idx") is not None and r.get("pass"):
                j = r["idx"]
                if isinstance(j, int) and 0 <= j < len(chunk):
                    passed.add(i + j)
    return passed


def _load_overlay_items(window):
    """叠层确证参与清单：出生窗严格早于本窗、达参与门的实体。

    原算法设计：叠层信号临时插入分类体系，与既有技能一起在分类任务中运行，
    观察市场分类响应（participating_items 合并三源 ΔG、按窗末重算强度）。
    """
    try:
        from participation import participating_items
        ws = date(int(window[:4]), int(window[5:7]), 1)
        items = participating_items(now=ws)
        return [it for it in items
                if it.get("born_window") and it["born_window"] < window]
    except Exception as e:
        print(f"[B] 叠层参与清单加载失败（确证通道本窗空转）：{e}", flush=True)
        return []


def _setting(*keys, default):
    try:
        import yaml
        with open(os.path.join(REPO, "codes", "settings.yaml"), encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except Exception:
        return default


def config_batch_size():
    """extractor 句批大小（settings → jd_extract.batch_size，与 extractor/config 同源兜底）。"""
    return _setting("jd_extract", "batch_size", default=15)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _taxonomy_meta():
    """体系基准元信息（file/version/n/sha256），供下游兼容性校验。"""
    out = {}
    for key, p in (("jobs", gconfig.BASE_NODE_FILES["jobs"]),
                   ("tasks", gconfig.BASE_NODE_FILES["tasks"]),
                   ("skills", gconfig.BASE_NODE_FILES["skills"])):
        try:
            data = json.load(open(p, encoding="utf-8"))
            n = len(data.get("detail") or data.get("tasks") or {})
            ver = data.get("version") or data.get("meta", {}).get("version", "")
            out[key] = {"file": os.path.relpath(p, REPO).replace("\\", "/"),
                        "version": ver, "n": n, "sha256": _sha256_file(p)}
        except OSError:
            out[key] = {"file": None}
    return out


def make_extractors():
    """构造 Extractor(merged)+text_split。merged 模式一句一次出技能+技能点+任务，
    替代 skill/task 两次分离调用（句级调用减半、不损穷举性）。

    跨模块 config 约定同 base_builder.make_extractors：extractor 子包的 `import config`
    需命中 extractor/config.py（CACHE_DIR/SENTENCE_* 等），临时换出 builder 版再恢复。
    注意：import graph_config 已把 codes/builder 推到 sys.path[0]，须把 extractor 重新置顶，
    否则 `import config` 会命中 builder/config.py（缺 CACHE_DIR）。
    """
    if _EXT_DIR in sys.path:
        sys.path.remove(_EXT_DIR)
    sys.path.insert(0, _EXT_DIR)
    saved = sys.modules.pop("config", None)
    try:
        import text_split
        from extractor import Extractor
        return Extractor(mode="merged"), text_split
    finally:
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)


def load_full_classification(csv_path, strict=True):
    """对 timeline CSV 全量做岗位归类（规则层在线 + LLM 缓存）→ (cls_map, stats)。

    优先读窗口级归类缓存 {窗口}.jobcls.json（A 门跑满后由 classify_job 落盘，本函数
    也会在自建跑满时落盘）——大窗（60 万行）collect 扫描 ~33 分钟，S/B 重复扫描纯浪费。
    缓存为 PRE-scope 原始形态，it_scope 过滤恒在此在线应用（范围调整无需重建缓存）。

    → {jd_key: {job_code, it_related, tier}}；it_scope 排除集内 → it_related=False，
    LLM 多岗结果取首个范围内岗位。
    """
    cls_raw, st = classify_job.read_jobcls_cache(csv_path, strict)
    from_cache = cls_raw is not None
    if not from_cache:
        # Stage S0 预抽样（2026-09-03）：jobcls 缺失时的在线重建同样只算已选键，
        # 与 A 门工作宇宙一致（正常管线 A 先跑、jobcls 已含过滤）
        cls_raw, st = classify_job.merged_classification(
            csv_path, None, strict=strict,
            presample=classify_job._load_presample_keys(csv_path))
    scope = load_it_scope()
    scope_excl = set(scope.get("exclude_jobs") or {})
    cls_map = {}
    n_out_scope = n_unclassified = 0

    def _entry(jobs):
        """多岗结果按 it_scope 过滤 → (job_code, it_related, out_of_scope)。"""
        nonlocal n_out_scope
        in_scope = [j for j in (jobs or []) if j not in scope_excl]
        if in_scope:
            return in_scope[0], True, False
        if jobs:
            n_out_scope += 1
            return jobs[0], False, True     # 全部岗位在范围外（保留码供追溯）
        return None, False, False           # 无岗：排除表/LLM 非IT/无法判断 → 均不进图谱

    for key, raw in cls_raw.items():
        jobs = raw.get("jobs") or []
        if raw.get("unclassified"):
            n_unclassified += 1
            cls_map[key] = {"job_code": None, "it_related": False, "tier": None,
                            "unclassified": True}
            continue
        job_code, it, oos = _entry(jobs)
        cls_map[key] = {"job_code": job_code, "it_related": it,
                        "tier": raw.get("tier"), "out_of_scope": oos}
    if not from_cache and n_unclassified == 0 and st.get("rows"):
        try:
            classify_job.write_jobcls_cache(csv_path, cls_raw, st, strict)
        except Exception as e:
            print(f"    [jobcls] 窗口缓存写入失败（不影响结果）：{e}", flush=True)
    elif from_cache:
        print(f"    [jobcls] 读窗口归类缓存（免扫描）："
              f"{os.path.basename(csv_path)[:-4]}.jobcls.json（{len(cls_raw)} 条）", flush=True)
    st["out_of_scope"] = n_out_scope
    st["it_scope_version"] = scope.get("scope_version", "")
    return cls_map, st


def _norm_ev(sents):
    """证据句集合 → 归一化指纹（排序+空白归一+md5），供 (技能,证据) 跨 JD 去重。"""
    norm = sorted(re.sub(r"\s+", " ", s).strip() for s in sents if s and s.strip())
    return hashlib.md5("∥".join(norm).encode("utf-8")).hexdigest()


# ---------------- 严格 IT 岗位范围（codes/graph/it_scope.json） ----------------

IT_SCOPE_PATH = os.path.join(HERE, "it_scope.json")
_IT_SCOPE = None


def load_it_scope():
    """排除岗位码集合（含版本信息懒加载）。文件见 codes/graph/it_scope.json。"""
    global _IT_SCOPE
    if _IT_SCOPE is None:
        _IT_SCOPE = json.load(open(IT_SCOPE_PATH, encoding="utf-8"))
    return _IT_SCOPE


# ---------------- skillpoint 后置清洗（品牌/设备/泛指 + 归一化兜底） ----------------

# prompt 已约束"品牌/设备/型号不算技能点"，此表为 LLM 漏网的兜底黑名单（消费品牌/工控
# 厂商/安防厂商等；实测长尾出现"天猫精灵/西门子/ZKT E320"类）
_SP_BLACKLIST = {
    "天猫精灵", "小米", "华为", "苹果", "大疆", "海康威视", "海康", "大华", "宇视",
    "西门子", "三菱", "欧姆龙", "松下", "基恩士", "汇川", "台达", "施耐德", "ABB",
    "罗克韦尔", "倍福", "菲尼克斯", "阿里", "腾讯", "百度", "字节", "抖音", "美团",
    "京东", "拼多多", "快手", "讯飞", "商汤", "旷视", "依图", "云从", "英伟达",
    "高通", "博通", "联发科", "紫光", "长江存储", "中芯国际", "寒武纪", "地平线",
    "泰凌", "瑞昱", "乐鑫", "博流", "沁恒", "兆易创新",
}
# 测量/生产设备（非可学习技术载体）：实测长尾出现"示波器/万用表/接地电阻测试仪"类。
# 注意 SPI/I2C/CAN 等总线协议是嵌入式软件（DEV-33）合法技能点，勿入此表。
_SP_DEVICES = {
    "示波器", "万用表", "电烙铁", "热风枪", "贴片机", "回流焊", "波峰焊", "AOI",
    "X-Ray", "三坐标", "二次元影像仪", "三次元", "投影仪", "卡尺", "千分尺",
    "钳表", "电桥", "频谱仪", "信号发生器", "逻辑分析仪", "耐压仪",
}
# 泛指词（非可考核技术实体）：实测长尾出现"平台层/软件系统/办公软件/二次元"类
_SP_GENERIC = {
    "平台层", "软件系统", "办公软件", "计算机", "电脑", "互联网", "大数据", "人工智能",
    "云计算", "二次元", "自动化", "数字化", "智能化", "系统", "平台", "软件", "硬件",
}
# 后缀规则：以这些词结尾的多为器件/设备罗列（"瑞昱芯片"/"XX测试仪"），非技能点
_SP_SUFFIX_DROP = ("芯片", "测试仪", "测量仪", "分析仪", "传感器", "电阻", "电容", "模组")
# 厂商前缀改写：写规格品名（"西门子PLC"→"PLC"），与 prompt 同口径
_SP_REWRITE_PREFIX = {b: ("PLC", "伺服", "变频器", "触摸屏", "WinCC") for b in
                      ("西门子", "三菱", "欧姆龙", "汇川", "台达", "松下", "施耐德", "基恩士")}
_SP_FW = str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
                       "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９",
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


def clean_skillpoints(sps):
    """技能点列表 → 清洗后列表：空白/全角归一、厂商前缀改写、黑名单与泛指剔除、去重。

    纯英文技能点大小写不敏感去重（保留首个拼写，如 Python/python 取先出现者）。
    """
    out, seen = [], {}
    for sp in sps:
        s = re.sub(r"\s+", " ", str(sp).translate(_SP_FW)).strip(" ，,、；;·")
        for brand, techs in _SP_REWRITE_PREFIX.items():
            if s.startswith(brand):
                for t in techs:
                    if s == brand + t:
                        s = t
                        break
        if not (2 <= len(s) <= 30) or s in _SP_BLACKLIST or s in _SP_GENERIC \
                or s in _SP_DEVICES or s.endswith(_SP_SUFFIX_DROP):
            continue
        k = s.lower()
        if k in seen:
            continue
        seen[k] = True
        out.append(s)
    return out


# ---------------- JD 分段（跳过福利/公司介绍等噪声段，实测 ~4% 句但干净无损） ----------------
_RESP_HDR = ["岗位职责", "工作内容", "职位描述", "工作职责", "主要职责", "职责描述",
             "工作说明", "岗位说明", "key objectives", "responsibilities",
             "responsibility", "job description", "what you will do", "your role"]
_REQ_HDR = ["任职要求", "招聘要求", "任职资格", "岗位要求", "任职条件", "招聘条件",
            "资历要求", "任职资历", "能力要求", "任职需求", "qualifications",
            "requirements", "job requirements", "我们希望你"]
_OTHER_HDR = ["福利", "公司介绍", "公司简介", "薪资", "工作地点", "工作时间", "联系方式",
              "晋升", "企业文化", "上班时间", "薪资待遇", "发展空间", "培训", "我们的",
              "about us", "团队介绍", "备注", "其他说明", "岗位福利", "员工福利",
              "公司福利", "福利待遇", "公司文化", "薪酬", "工作环境", "应聘", "投递",
              "简历", "面试", "联系电话", "邮箱"]


def _classify_header(line):
    s = (line or "").strip()
    if not s:
        return None
    inner = re.sub(r"^[【\[【\(]+|\】【\]\)】]+$", "", s).strip()
    cand = inner.rstrip("：:。: ：")
    low = cand.lower()
    for lab in _RESP_HDR:
        if lab in low and len(cand) <= len(lab) + 6:
            return "resp"
    for lab in _REQ_HDR:
        if lab in low and len(cand) <= len(lab) + 6:
            return "req"
    for lab in _OTHER_HDR:
        if lab in low and len(cand) <= len(lab) + 6:
            return "other"
    return None


def _kept_text(text):
    """JD 文本 → 去掉福利/公司介绍等 other 段后的文本（职责+需求+未识别段保留）。

    无任何可识别段头（~12% JD）则原样返回（退整体）。jd_text_key 仍用全文（与 Stage A
    岗位归类缓存同口径 join），分段只作用于抽取输入。
    """
    lines = text.splitlines()
    secs, cur_kind, cur = [], "unknown", []
    for line in lines:
        kind = _classify_header(line)
        if kind:
            if cur:
                secs.append((cur_kind, cur))
            cur_kind, cur = kind, []
        else:
            cur.append(line)
    if cur:
        secs.append((cur_kind, cur))
    kept = [l for kind, ls in secs for l in ls if kind != "other"]
    return "\n".join(kept) if kept else text


def _load_near_dup_variants(window):
    """Stage D0 近重复（抄袭）变体键集（{窗口}.dedup.json，缺失=空，向后兼容）。"""
    import jd_dedup
    variants = jd_dedup.load_variants(window)
    if variants:
        print(f"[B] 近重复过滤：剔除抄袭变体 {len(variants)} 条（{window}.dedup.json）")
    return variants


def run(window, limit=None, force=False):
    csv_path = os.path.join(TIMELINE_JD_DIR, f"{window}.csv")
    if not os.path.exists(csv_path):
        sys.exit(f"[ERR] timeline CSV 不存在：{csv_path}")
    out_jsonl = os.path.join(gconfig.JD_DERIVED_DIR, f"{window}.jd_vectors.jsonl")
    out_meta = os.path.join(gconfig.JD_DERIVED_DIR, f"{window}.jd_vectors.meta.json")
    if os.path.exists(out_jsonl) and not force:
        sys.exit(f"[ERR] 源文件已存在：{out_jsonl}（--force 覆盖）")

    min_text_chars = gconfig.GB_MIN_TEXT_CHARS
    sal_w = gconfig.GB_SALARY_WEIGHT
    strict = _setting("jd_gate", "strict", default=True)

    print(f"[B] 加载岗位归类（classify_job {'严格门(岗位名直收/关键词送LLM)' if strict else '词库快路'}"
          f" + jd_job_cache + it_scope 范围过滤）...", flush=True)
    cls_map, st = load_full_classification(csv_path, strict=strict)
    n_it = sum(1 for c in cls_map.values() if c.get("it_related"))
    n_nonit = len(cls_map) - n_it
    print(f"    行 {st['rows']} → 去重 {st['unique']} | IT范围内 {n_it} | 非IT/范围外 {n_nonit}"
          f"（排除表 {st['excluded']} / 送LLM {st['miss']} / 岗位范围外 {st.get('out_of_scope', 0)}）", flush=True)

    # Stage S 降采样：{window}.sample.json 存在且 keys 非空 → 只抽采样键（sample_weight 随记录写出）
    sample_path = os.path.join(gconfig.JD_DERIVED_DIR, gconfig.JD_SAMPLE_FILENAME.format(window=window))
    sample_keys = None
    sampling_meta = None
    if os.path.exists(sample_path):
        srec = json.load(open(sample_path, encoding="utf-8"))
        sampling_meta = {"file": os.path.relpath(sample_path, REPO).replace("\\", "/"),
                         "sampled": bool(srec.get("sample", {}).get("sampled")),
                         "n_population": srec.get("population", {}).get("it_in_scope"),
                         "n_sampled": srec.get("sample", {}).get("n_sampled"),
                         "effective_rate": srec.get("sample", {}).get("effective_rate"),
                         "params": srec.get("params")}
        if srec.get("keys"):
            sample_keys = srec["keys"]
            print(f"    [S] 降采样生效：总体 {sampling_meta['n_population']} → 采样 "
                  f"{sampling_meta['n_sampled']}（有效率 {sampling_meta['effective_rate']:.1%}，"
                  f"逆概率加权）", flush=True)
        else:
            print(f"    [S] sample.json 存在但未降采样（全量保留），只记元信息", flush=True)

    print(f"[B] 构造抽取器（Extractor merged：一句一次 skill+task+skillpoint，跳过 other 段）...", flush=True)
    ext, text_split = make_extractors()
    stack_matchers = ann_common.StackMatchers(ann_common.load_taxonomy())   # 技术栈规则（零 LLM）

    # ---- 叠层确证参与（原算法设计）：出生窗早于本窗、达参与门的叠层实体临时插入
    # 分类体系，与既有技能/任务一起在分类任务中运行，观察市场分类响应 → Pass 4 落
    # require 级确证证据（转正唯一口径）。替代 jd_delta_v2 的子串预筛确证通道。
    # 分工：任务/技能=句级分类（语义命中含同义/改写）；**岗位=JD 标题级批量分类**
    # （句级实测零命中——句子描述"做什么"归技能/任务，不关联角色画像；岗位的
    # 天然单元是标题/整条 JD，与原旧路径整文档投喂同语义）。
    overlay_items = _load_overlay_items(window)
    ov_jobs = [it for it in overlay_items if it.get("array") == "new_jobs"]
    ov_sent = [it for it in overlay_items if it.get("array") != "new_jobs"]
    overlay_by_name = {}
    for _it in overlay_items:
        overlay_by_name.setdefault(_it["name_zh"], []).append(_it)
    if ov_sent:
        ext.set_overlay_items(ov_sent)
        print(f"[B] 叠层候选参与句级分类：{len(ov_sent)} 任务/技能"
              + (f"；另有 {len(ov_jobs)} 岗位走标题级确证（Pass 3.7）" if ov_jobs else ""),
              flush=True)

    # ---- Pass 1：扫描收集（门过滤 + 采样键 + 分段分句），不做 LLM ----
    # 两遍式（窗口级并行）：旧版逐 JD 调用分类，每 JD 仅 1 批、并发无从发挥，大窗串行
    # 需数小时；现先收集全窗唯一句，一次窗口级分类（批间 llm.concurrency 并发），
    # 再逐 JD 组装（全缓存命中，零 LLM）。句集/缓存/口径与逐 JD 完全一致。
    seen = set()
    n_written = n_skip_short = n_no_signal = n_skip_sampled = 0
    n_pairs = 0                          # 全部 (JD, 技能) 证据对（含跨 JD 重复）
    ev_keys = set()                      # 唯一 (技能, 归一化证据)
    skills_seen = set()
    per_job = Counter()
    queue = []                           # 待组装 JD（key/row/sentences/cls）
    all_sentences = {}                   # 跨 JD 有序唯一句集
    near_dup_variants = _load_near_dup_variants(window)   # Stage D0 抄袭变体（存在则过滤）

    with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if limit and len(queue) >= limit:
                break
            title = (row.get("job") or "").strip()
            text = (row.get("job_information") or row.get("job") or "").strip()
            key = ann_common.jd_text_key(title, text)
            if key in seen:
                continue
            seen.add(key)
            if key in near_dup_variants:
                n_skip_sampled += 1
                continue                       # Stage D0 近重复：抄袭变体不进抽取（防频次虚增）
            cls = cls_map.get(key)
            if not cls or not cls.get("it_related"):
                continue                       # 非 IT 跳过
            if sample_keys is not None and key not in sample_keys:
                n_skip_sampled += 1
                continue                       # Stage S 降采样：非采样键跳过
            if len(text) < min_text_chars:
                n_skip_short += 1
                continue                       # 文本过短：无技能可抽，跳过

            per_job[cls["job_code"]] += 1
            # 注：per_job 计全部通过 A 门的 JD；无信号降级在记录级以 it_related=False 体现
            kept = _kept_text(text)                       # 跳过福利/公司介绍等 other 段
            sentences = text_split.split_sentences(kept)
            queue.append({"key": key, "row": row, "title": title, "text": text,
                          "cls": cls, "sentences": sentences})
            for s in sentences:
                all_sentences[s] = True

    # ---- Pass 2：窗口级句批并行分类（结果落句级缓存；超批切片仅为进度/容错粒度）----
    sent_list = list(all_sentences)
    del all_sentences
    _SUPER = 3000
    if sent_list:
        print(f"[B] Pass 2 窗口级分类：{len(sent_list)} 唯一句（{len(queue)} JD），"
              f"批 {config_batch_size()} 句 × 并发 {ext.llm.concurrency}...", flush=True)
    for i in range(0, len(sent_list), _SUPER):
        ext._classify_units(sent_list[i:i + _SUPER], None)
        cs = ext.cache.stats() if ext.cache else {}
        print(f"    句级分类 {min(i + _SUPER, len(sent_list))}/{len(sent_list)}"
              f"（LLM {ext.llm.stats()}，缓存命中 {cs.get('hits', '?')}）", flush=True)

    # ---- Pass 3：逐 JD 组装（全缓存命中，零 LLM）→ skillpoint 三层归一 → 写出 ----
    from skillpoint_norm import SkillpointNormalizer
    os.makedirs(gconfig.JD_DERIVED_DIR, exist_ok=True)
    records_out = []
    for item in queue:
        row, title, text = item["row"], item["title"], item["text"]
        key, cls, sentences = item["key"], item["cls"], item["sentences"]
        job_code = cls["job_code"]

        # 句级抽取（merged：一句一次出 skill+task+skillpoint；取 results 建 evidence_map）
        res, agg = ext._classify_units(sentences, None)
        skill_counts = agg.get("skill_counts", {})
        task_counts = agg.get("task_counts", {})
        sp_map_raw = agg.get("skill_skillpoint_map", {})

        skill_vec_01 = sorted(skill_counts)
        task_vec_01 = sorted(task_counts)
        skillpoint_map = {sk: clean_skillpoints(sp_map_raw[sk].keys())
                          for sk in sp_map_raw}
        skillpoint_map = {sk: sps for sk, sps in skillpoint_map.items() if sps}

        # evidence_map（同 jd_proficiency._classify_evidence 口径：聚合技能剔除、保序）
        evidence_map = {}
        for s in sentences:
            rec = res.get(s) or {"skills": [], "tasks": []}
            for m in rec.get("skills", []):
                code = m.get("code")
                if not code or code in _AGGREGATE_SKILLS:
                    continue
                lst = evidence_map.setdefault(code, [])
                if s not in lst:
                    lst.append(s)

        # 叠层确证命中（名称 → 首句证据；不进 skill/task 向量，基图统计不受影响）
        overlay_confirm = {}
        for s in sentences:
            for nm in (res.get(s) or {}).get("overlays", []):
                overlay_confirm.setdefault(nm, s)
        for code, sents in evidence_map.items():
            n_pairs += 1
            ev_keys.add((code, _norm_ev(sents)))
            skills_seen.add(code)

        # 技术栈/职级（规则，零 LLM）
        stacks, _tier = ann_common.rule_stacks(stack_matchers, title, text)
        level, level_source = annotate_jd.resolve_level(
            row.get("work_year") or "", title, text, row.get("funtype") or "")

        # 无技术信号降级：技能/任务/技术栈全空 → 内容无 IT 信号（多为泛词误报漏网的
        # 非 IT JD），标 it_related=False 不进图谱（下游 D/汇总按该旗标过滤）
        it_related, drop_reason = True, None
        if not skill_vec_01 and not task_vec_01 and not stacks:
            it_related = False
            drop_reason = "no_tech_signal"

        rec = {
            "jd_key": key,
            "jobid": row.get("jobid", ""),
            "opentime": row.get("opentime", ""),
            "title": title,
            "funtype": row.get("funtype", ""),   # 源数据平台多选字段（" or " 分隔），非单值
            "job_code": job_code,
            "it_related": it_related,
            "tier": cls.get("tier"),
            "techstack": stacks,
            "level": level,
            "level_source": level_source,
            "salary": row.get("salary", ""),
            "salary_monthly": base_builder.parse_salary_monthly(row.get("salary") or ""),
            "salary_weight": 1.0,        # salary_weight=false → 等权；true 需按窗口 median 重算（本阶段固定 1.0）
            "sample_weight": round(sample_keys.get(key, 1.0), 4) if sample_keys else 1.0,  # Stage S 逆概率权重（N_j/k_j）
            "work_year": row.get("work_year", ""),
            "skill_vec_01": skill_vec_01,
            "task_vec_01": task_vec_01,
            "skillpoint_map": skillpoint_map,
            "evidence_map": evidence_map,
            "skill_vec_prof": {},         # Stage C 回填
        }
        if overlay_confirm:
            rec["overlay_confirm"] = overlay_confirm
        if drop_reason:
            rec["drop_reason"] = drop_reason
        records_out.append(rec)
        n_written += 1
        if drop_reason:
            n_no_signal += 1
        if n_written % 500 == 0:
            cs = ext.cache.stats() if ext.cache else {}
            print(f"    已组装 {n_written} JD（merged 句级缓存命中 {cs.get('hits', '?')}）", flush=True)

    # ---- Pass 3.5：skillpoint 三层归一（L1 字面折叠/L2 注册表零成本；L3 未知名批 LLM）----
    n_sp_raw = sum(len(sps) for r in records_out for sps in (r.get("skillpoint_map") or {}).values())
    sp_norm = SkillpointNormalizer(llm_post=ext.llm._post, use_cache=True)
    sp_norm_stats = {}
    if records_out:
        print(f"[B] Pass 3.5 skillpoint 归一（{n_sp_raw} 实例；L1/L2 免费，未知名 LLM 首见归一）...",
              flush=True)
        for r in records_out:
            if r.get("skillpoint_map"):
                r["skillpoint_map"] = sp_norm.normalize_skillpoint_map(r["skillpoint_map"])
        sp_norm_stats = {**sp_norm.stats,
                         "n_instances": n_sp_raw,
                         "n_instances_canonical": sum(len(sps) for r in records_out
                                                       for sps in (r.get("skillpoint_map") or {}).values()),
                         "n_unique_canonical": len({sp for r in records_out
                                                    for sps in (r.get("skillpoint_map") or {}).values()
                                                    for sp in sps}),
                         "registry_version": sp_norm.registry_version}
        print(f"    归一统计：{sp_norm_stats}", flush=True)

    # ---- Pass 3.7：叠层岗位确证（JD 标题级批量分类，宁缺毋滥）----
    # 命中并入 rec["overlay_confirm"]（与句级任务/技能命中同流），Pass 4 统一落证据。
    if ov_jobs:
        _EXT = _EXT_DIR
        if _EXT in sys.path:
            sys.path.remove(_EXT)
        sys.path.insert(0, _EXT)
        _saved_cfg = sys.modules.pop("config", None)
        try:
            import prompts as ext_prompts
        finally:
            sys.path.remove(_EXT)
            if _saved_cfg is not None:
                sys.modules["config"] = _saved_cfg
            else:
                sys.modules.pop("config", None)
        job_items = {it["name_zh"]: it for it in ov_jobs}
        job_label_lines = []
        for it in ov_jobs:
            dfn = (it.get("definition") or "").replace(chr(10), " ")
            if len(dfn) > 90:
                dfn = dfn[:90] + "…"
            job_label_lines.append(f"- {it['name_zh']}：{dfn}" if dfn else f"- {it['name_zh']}")
        ov_jobs_text = chr(10).join(job_label_lines)
        ov_names = set(job_items)
        BATCH_T = 10   # 批尺寸敏感（实证：≤7 行稳定、19 行混批判定漂移）：宁小勿大
        # 逐 JD 判定（标题+正文信号联合，2026-08-30 修订：标题字面命中无法消歧多义
        # 头衔——"产品工程师"在制造/硬件语境 ≠ 软件产品工程，须靠正文信号判定领域）
        ext._ensure_merged_tax()
        sk_names = ext._skill_tax.code_to_name
        tk_names = ext._task_tax.code_to_name
        cand_rows = []      # (record_idx, title, context)
        for i, r in enumerate(records_out):
            if not r.get("it_related", True):
                continue
            t = (r.get("title") or "").strip()
            if not t:
                continue
            sk = [sk_names.get(c, c) for c in (r.get("skill_vec_01") or [])][:5]
            tk = [tk_names.get(c, c) for c in (r.get("task_vec_01") or [])][:4]
            tech_l = list(r.get("techstack") or [])[:4]
            parts = []
            if sk:
                parts.append("技能：" + "、".join(sk))
            if tk:
                parts.append("任务：" + "、".join(tk))
            if tech_l:
                parts.append("技术栈：" + "、".join(tech_l))
            ctx = "；".join(parts) if parts else "（正文无技术信号）"
            cand_rows.append((i, t, ctx))
        print(f"[B] Pass 3.7 叠层岗位确证：{len(cand_rows)} JD（标题+正文信号）× {len(ov_jobs)} 前瞻岗位"
              f"（批 {BATCH_T}）...", flush=True)
        n_title_hits = 0

        def _job_batch(chunk):
            payload = json.dumps(
                [{"idx": j, "title": t, "signals": ctx}
                 for j, (_i, t, ctx) in enumerate(chunk)], ensure_ascii=False)
            prompt = (ext_prompts.PROMPT_JOB_OVERLAY
                      .replace("{overlay_jobs}", ov_jobs_text)
                      .replace("{titles}", payload))
            try:
                return ext.llm._post(prompt) or []
            except Exception as e:
                print(f"    [B] 岗位确证批失败（留待下窗）：{e}", flush=True)
                return []

        from concurrent.futures import ThreadPoolExecutor
        chunks = [cand_rows[b0:b0 + BATCH_T] for b0 in range(0, len(cand_rows), BATCH_T)]
        with ThreadPoolExecutor(max_workers=ext.llm.concurrency) as ex:
            for chunk, rows in zip(chunks, ex.map(_job_batch, chunks)):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    j = row.get("idx")
                    if not isinstance(j, int) or not (0 <= j < len(chunk)):
                        continue
                    ri, t, ctx = chunk[j]
                    hits = [nm for nm in (row.get("jobs") or [])
                            if isinstance(nm, str) and nm in ov_names]
                    if not hits:
                        continue
                    n_title_hits += 1
                    oc = records_out[ri].setdefault("overlay_confirm", {})
                    sig = ctx if len(ctx) <= 100 else ctx[:100] + "…"
                    for nm in hits:
                        if nm not in oc:
                            oc[nm] = f"JD标题：{t}｜正文信号：{sig}"
        print(f"[B] Pass 3.7 完成：{n_title_hits} JD 命中前瞻岗位（标题+信号联合判定）", flush=True)

    with open(out_jsonl, "w", encoding="utf-8") as fout:
        for rec in records_out:
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- Pass 4：叠层确证证据落 ΔG（require 级；同 doc_id 幂等，转正的唯一确证口径）----
    # 落库前经**独立复核门**（PROMPT_OVERLAY_RECHECK）：句级/标题级分类在混批上下文
    # 与采样下存在判定漂移（实证：同句 5 次重复 1 次松命中，干净批 0 次），与论文
    # 通道 recheck_keeps 同型问题——第二道守门，宁严勿宽，未过不落证据。
    ov_stats = {"n_entities_injected": len(overlay_items), "n_jds_with_hits": 0,
                "n_require_evidence": 0, "n_entities_hit": 0,
                "n_recheck_pairs": 0, "n_recheck_passed": 0}
    if overlay_items:
        import types
        from delta_store import DeltaStore
        # 收集 (记录, 实体项, 证据句) 三元组 → 批量复核 → 仅通过者落库
        pairs = []
        for rec in records_out:
            for nm, sent in (rec.get("overlay_confirm") or {}).items():
                for it in overlay_by_name.get(nm, []):
                    pairs.append((rec, it, sent))
        passed = set()
        if pairs:
            passed = _recheck_overlay_pairs(ext, pairs)
            ov_stats["n_recheck_pairs"] = len(pairs)
            ov_stats["n_recheck_passed"] = len(passed)
        store = DeltaStore(builder_config.JD_DELTA_OUTPUT,
                           source_desc=f"JD 叠层确证参与（窗口 {window}）",
                           source_kind="jd", now=_window_end_date(window))
        entities_hit = set()
        jds_hit = set()
        for pi, (rec, it, sent) in enumerate(pairs):
            if pi not in passed:
                continue
            shim = types.SimpleNamespace(
                doc_id=rec.get("jobid") or rec["jd_key"],
                pub_date=(rec.get("opentime") or "")[:10])
            store.confirm_named(it["array"], it["name_zh"], shim, [sent[:300]], "high",
                                definition=it.get("definition") or "",
                                ref_id=it.get("id") or "", grade="require")
            ov_stats["n_require_evidence"] += 1
            entities_hit.add(it["name_zh"])
            jds_hit.add(rec["jd_key"])
        ov_stats["n_jds_with_hits"] = len(jds_hit)
        ov_stats["n_entities_hit"] = len(entities_hit)
        store.save()
        print(f"[B] Pass 4 叠层确证：复核 {ov_stats['n_recheck_passed']}/{ov_stats['n_recheck_pairs']} 通过 → "
              f"{ov_stats['n_jds_with_hits']} JD 命中 {ov_stats['n_entities_hit']}/{len(overlay_items)} 实体 → "
              f"{ov_stats['n_require_evidence']} 条 require 证据 → {builder_config.JD_DELTA_OUTPUT}",
              flush=True)

    # 抽取后统计
    s_merged = ext.stats()

    meta = {
        "schema_version": "0.1",
        "stage": "B_extract",
        "window": window,
        "created": datetime.now().isoformat(timespec="seconds"),
        "producer": "codes/graph/run_jd_extract.py",
        "taxonomy": _taxonomy_meta(),
        "llm": {"model": _setting("llm", "model", default="deepseek-v4-flash"),
                "use_thinking": _setting("llm", "use_thinking", default=True)},
        "params": {"sample_total": None, "per_job": None,
                   "salary_weight": sal_w, "min_text_chars": min_text_chars,
                   "strict_gate": strict,
                   "level_rules": annotate_jd.LEVEL_RULES_VERSION},
        "it_scope": {"file": os.path.relpath(IT_SCOPE_PATH, REPO).replace("\\", "/"),
                     "scope_version": st.get("it_scope_version", ""),
                     "excluded_jobs": len(load_it_scope()["exclude_jobs"]),
                     "out_of_scope_jds": st.get("out_of_scope", 0)},
        "sampling": sampling_meta or {"file": None, "sampled": False,
                                      "note": "无 {窗口}.sample.json，全量处理"},
        "skillpoint_norm": sp_norm_stats or {"note": "本窗无 skillpoint"},
        "counts": {
            "csv_rows": st["rows"], "unique": st["unique"], "it": n_it, "non_it": n_nonit,
            "classified_written": n_written, "skip_short_text": n_skip_short,
            "skip_not_sampled": n_skip_sampled,
            "no_tech_signal_dropped": n_no_signal,
            "written_it_related": n_written - n_no_signal,
            "excluded_non_it": st["excluded"], "llm_pending": st["miss"],
            "per_job_code": dict(per_job.most_common()),
        },
        "source_csv": os.path.relpath(csv_path, REPO).replace("\\", "/"),
        "extraction_mode": "merged 两遍式（窗口级句批并行：Pass1 扫描分句 → Pass2 全窗唯一句批并发分类 → Pass3 逐 JD 组装全缓存命中；一句一次 skill+task+skillpoint；跳过 other 段；skillpoint 后置清洗）",
        "caches": {
            "sentence_merged": "codes/extractor/cache/cache_merged_v3.jsonl",
            "job_classification": os.path.relpath(JD_JOB_CACHE, REPO).replace("\\", "/"),
        },
        "extractor_stats": {
            "merged_cache": s_merged.get("cache", {}),
            "llm": s_merged.get("llm", {}),   # {"calls": N, "tokens": M} 供成本估算
        },
        "overlay_confirm": ov_stats,   # 叠层确证参与（Pass 4：require 证据 → jd_delta.json）
        "evidence_dedup": {
            "total_skill_jd_pairs": n_pairs,
            "unique_skill_evidence_pairs": len(ev_keys),
            "dedup_rate": round(1 - len(ev_keys) / max(n_pairs, 1), 3) if n_pairs else 0,
            "skills_seen": sorted(skills_seen),
        },
        "vector_versions": {
            "skill_vec_01": "sorted present codes (49-space, 0/1, for aggregation)",
            "skill_vec_prof": "P1-P4/U (43 gradeable skills; Stage C backfill, currently {})",
            "task_vec_01": "sorted present task codes",
            "evidence_map": "{skill: [evidence sentences]} (Stage C input; 6 aggregate skills excluded)",
        },
        "notes": "skill_vec_prof 空，待 Stage C 熟练度回填",
    }
    with open(out_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"\n[B] 完成：{out_jsonl}", flush=True)
    print(f"    写出 {n_written} JD（it_related {n_written - n_no_signal}"
          f" / 无技术信号降级 {n_no_signal}；跳过短文本 {n_skip_short}"
          + (f" / 降采样跳过 {n_skip_sampled}" if n_skip_sampled else "") + "）", flush=True)
    print(f"    句级缓存（merged）：{s_merged.get('cache', {})}", flush=True)
    print(f"    LLM 用量：{s_merged.get('llm', {})}（calls/tokens，供成本估算）", flush=True)
    print(f"    证据去重：(JD×技能) 对 {n_pairs} → 唯一(技能,证据) {len(ev_keys)}"
          f"（去重率 {meta['evidence_dedup']['dedup_rate']:.1%}）", flush=True)
    print(f"    meta：{out_meta}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Stage B：全量 IT JD 抽取 → jd_vectors 源文件")
    ap.add_argument("--window", required=True, help="窗口（YYYY-MM，如 2025-10）")
    ap.add_argument("--limit", type=int, default=None, help="最多写出 JD 条数（测试用）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的源文件")
    args = ap.parse_args()
    run(args.window, args.limit, args.force)


if __name__ == "__main__":
    main()
