# -*- coding: utf-8 -*-
"""岗位分类体系 v2.0 构建（2026-08-20）：人工骨架 + LLM 定义生成。

背景（docs/job_classification.json v1.1，255 节点）的问题（审计见 temp/job_taxonomy_audit.txt）：
  - 56 个"顶层节点"混合类别与具体岗位；4 个类别自带 funtypes 与子岗位争抢数据
  - 父子重合命名 21 对；跨支相近岗位 14 组（售前/售后 8 节点、C/C++ 4 节点、电气 3 节点…）
  - 16 个空壳节点（无子类无 funtypes 0 命中）；funtype 挂载覆盖仅 86.1%（字符规范化缺失 + 漏挂本名）
  - 大量低 IT 相关岗位（维修/楼宇/计量/职能类）混在体系中

v2.0 设计（方案见 temp/岗位体系调整方案.md，已审定）：
  1. **类别/岗位两级职责分离**：9 个一级类别纯组织维度（不挂 funtypes、不进 prompt 标签、
     不进图节点）；132 个二级岗位为唯一实体
  2. 相近岗位合并（194 个 v1 节点归并为 132 岗，aliases/source_codes 可追溯）
  3. 低 IT 相关岗位整体剔除（45 节点 + 9 个纯类别节点不进 v2；方案 T1/T2 清单）
  4. funtype 尽可能包容：保留域的 v1 funtypes 全量继承 + 9 个漏挂/规范化差异 part 补挂
  5. **归类不再依赖 funtype**：后续岗位归类走逐 JD 关键词+LLM（同 classify_stacks 模式），
     本体系为此提供 keywords 快路词库与 definition 判别上下文
  6. 每个岗位的定义由 LLM 阅读数据集中的真实 JD 样例生成（--sample → --define）

产物：classify/Jobs/jobs_v2.json（categories + detail；jobs0806.json 保留为 v1 存档，
运行时消费者切换待归类引擎就绪后进行）。

用法：
  python build_jobs.py --validate              # 校验 255 节点全量处置 + funtype 覆盖统计
  python build_jobs.py --sample                # 扫描 JD 数据集抽样（每岗 ≤4 条，funtype/标题匹配）
  python build_jobs.py --define [--limit N]    # 逐岗调 LLM 生成定义/关键词（断点续跑）
  python build_jobs.py --finalize              # 合并骨架+定义 → classify/Jobs/jobs_v2.json
  python build_jobs.py --doc                   # 由 jobs_v2.json 生成可读介绍文档
                                              #   introduction/岗位分类体系介绍.md（岗位+描述，随体系同步）
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

import common

HERE = os.path.dirname(os.path.abspath(__file__))
V1_PATH = os.path.join(common.REPO, "classify", "Jobs", "jobs0806.json")
OUT_PATH = os.path.join(common.REPO, "classify", "Jobs", "jobs_v2.json")
SAMPLES_PATH = os.path.join(common.OUT_DIR, "jobs_samples.json")
DEF_CACHE = os.path.join(common.OUT_DIR, "jobs_def_cache.jsonl")
PART_FREQ_CACHE = os.path.join(common.OUT_DIR, "jobs_part_freq_cache.json")
INTRO_PATH = os.path.join(common.REPO, "introduction", "岗位分类体系介绍.md")

# ---------------- v2.0 骨架（人工审定） ----------------

CATEGORIES = [
    ("DEV", "软件开发", "Software Development",
     "软件设计、编码与交付：后端/前端/移动/游戏/嵌入式等软件开发岗"),
    ("AID", "人工智能与数据", "AI & Data",
     "算法研究与工程化、数据工程与分析"),
    ("QA", "测试与质量", "Quality Assurance",
     "软件测试执行、测试开发与测试管理"),
    ("OPS", "运维与IT支持", "Operations & IT Support",
     "系统运维、数据库管理、IT 支持与技术服务"),
    ("NET", "网络与通信", "Network & Telecom",
     "网络工程与电信技术研发"),
    ("HW", "硬件与半导体", "Hardware & Semiconductor",
     "硬件设计与芯片设计/验证（不含制造工艺与设备）"),
    ("SEC", "安全", "Security",
     "网络安全、安全测试与密码/取证"),
    ("PD", "产品与设计", "Product & Design",
     "互联网/IT 产品岗与体验设计岗"),
    ("MGT", "技术管理", "Technology Management",
     "技术条线管理与技术项目管理"),
]

# (code, name_zh, [v1 source codes], [extra funtype parts], {title_kw: 标题抽样兜底词})
JOBS = [
    # ---- DEV 软件开发 ----
    ("DEV-01", "Java开发工程师", ["0121"], [], {}),
    ("DEV-02", "Python开发工程师", ["0124"], [], {}),
    ("DEV-03", "PHP开发工程师", ["0120"], [], {}),
    ("DEV-04", "Golang开发工程师", ["0152"], [], {}),
    ("DEV-05", "Node.js开发工程师", ["0158"], [], {}),
    ("DEV-06", "C#开发工程师", ["0153"], [], {}),
    ("DEV-07", ".NET开发工程师", ["0126"], [], {}),
    ("DEV-08", "Ruby开发工程师", ["0151"], [], {}),
    ("DEV-09", "C/C++开发工程师", ["0122", "A0JR", "0157", "0156"], ["C/C++开发工程师"], {}),
    ("DEV-10", "软件工程师", ["0107", "0106"], [], {}),
    ("DEV-11", "全栈工程师", ["0154"], [], {}),
    ("DEV-12", "架构师", ["0143", "7405"], ["网站架构设计师"], {}),
    ("DEV-13", "系统分析/需求工程师", ["0123", "6609"], [], {}),
    ("DEV-14", "脚本开发工程师", ["0132"], [], {}),
    ("DEV-15", "GIS工程师", ["0155"], [], {}),
    ("DEV-16", "爬虫工程师", ["0131"], [], {}),
    ("DEV-17", "区块链开发工程师", ["0128"], [], {}),
    ("DEV-18", "ERP技术开发", ["0117"], [], {}),
    ("DEV-19", "音视频/图形开发工程师", ["0133"], ["多媒体开发工程师"],
     {"title_kw": ["音视频", "流媒体", "图形开发", "视频开发"]}),
    ("DEV-20", "Web前端开发", ["7201", "7200"], [], {}),
    ("DEV-21", "HTML5开发工程师", ["7202"], [], {}),
    ("DEV-22", "移动开发工程师", ["7703", "7700"], [], {}),
    ("DEV-23", "Android开发工程师", ["7701"], [], {}),
    ("DEV-24", "iOS开发工程师", ["7702"], [], {}),
    ("DEV-25", "小程序开发工程师", ["7705"], [], {}),
    ("DEV-26", "鸿蒙开发工程师", ["A0N6"], [], {"title_kw": ["鸿蒙", "HarmonyOS", "Harmony"]}),
    ("DEV-27", "游戏开发工程师", ["7809", "7800"], [], {}),
    ("DEV-28", "UE4/UE5开发工程师", ["7823"], [], {"title_kw": ["UE4", "UE5", "Unreal", "虚幻"]}),
    ("DEV-29", "Unity3D开发工程师", ["7811"], [], {"title_kw": ["Unity", "U3D"]}),
    ("DEV-30", "Cocos开发工程师", ["7810"], [], {"title_kw": ["Cocos", "CocosCreator"]}),
    ("DEV-31", "游戏客户端开发工程师", ["7812"], [], {}),
    ("DEV-32", "游戏服务端开发工程师", ["7813"], [], {}),
    ("DEV-33", "嵌入式软件开发", ["A0MZ", "2910", "2909"],
     ["嵌入式软件开发(Linux/单片机/PLC/DSP…)", "电子软件开发(ARM/MCU...)"], {}),
    # ---- AID 人工智能与数据 ----
    ("AID-01", "算法工程师", ["7309"], [], {}),
    ("AID-02", "机器学习工程师", ["7301"], [], {}),
    ("AID-03", "深度学习工程师", ["7302"], [], {}),
    ("AID-04", "自然语言处理(NLP)工程师", ["7308"], [], {}),
    ("AID-05", "语音识别工程师", ["7306"], [], {}),
    ("AID-06", "搜索算法工程师", ["7311"], [], {}),
    ("AID-07", "推荐算法工程师", ["7310"], [], {}),
    ("AID-08", "图像算法工程师", ["7303", "7304", "7305", "7307"], [], {}),
    ("AID-09", "智能驾驶工程师", ["7120"], [], {"title_kw": ["自动驾驶", "智驾", "规控"]}),
    ("AID-10", "数据分析师", ["7501"], [], {}),
    ("AID-11", "数据开发工程师", ["0130", "0129"], [], {}),
    ("AID-12", "ETL开发工程师", ["7503"], [], {}),
    ("AID-13", "数据仓库工程师", ["7505"], [], {}),
    ("AID-14", "数据建模工程师", ["7507"], [], {}),
    ("AID-15", "数据治理工程师", ["7508"], [], {}),
    ("AID-16", "数据采集工程师", ["7506"], [], {}),
    ("AID-17", "BI工程师", ["7504"], [], {}),
    ("AID-18", "数据标注师", ["7512"], [], {}),
    ("AID-19", "数据分析经理/主管", ["7502"], [], {}),
    # ---- QA 测试与质量 ----
    ("QA-01", "测试工程师", ["2725", "2700", "2707", "2718", "2706", "2721", "2719"], [], {}),
    ("QA-02", "测试开发工程师", ["2722", "2720"], [], {}),
    ("QA-03", "游戏测试", ["7821"], [], {}),
    ("QA-04", "智能驾驶测试工程师", ["7124"], [], {}),
    ("QA-05", "测试经理/主管", ["2705", "2723", "2726"], [], {}),
    # ---- OPS 运维与IT支持 ----
    ("OPS-01", "运维工程师", ["7901", "7920", "7907"], [], {}),
    ("OPS-02", "运维开发工程师", ["7915"], [], {}),
    ("OPS-03", "系统工程师", ["7902"], [], {}),
    ("OPS-04", "系统集成工程师", ["7904"], [], {}),
    ("OPS-05", "DBA", ["7903"], [], {}),
    ("OPS-06", "ERP实施顾问", ["7905"], [], {}),
    ("OPS-07", "IT技术支持", ["A0LA", "7914"], [], {}),
    ("OPS-08", "技术支持工程师", ["3207", "8401", "8402", "7909"], ["技术支持/维护工程师"], {}),
    ("OPS-09", "技术支持经理/主管", ["3205", "3206", "8403", "8404", "7908"], ["技术支持/维护经理"], {}),
    ("OPS-10", "IT经理/IT主管", ["7912"], ["IT经理/IT主管"], {}),
    ("OPS-11", "配置管理工程师", ["7910"], [], {}),
    ("OPS-12", "技术文档工程师", ["0150", "2812", "6747", "2953"], [], {}),
    # ---- NET 网络与通信 ----
    ("NET-01", "网络工程师", ["7913", "A0LB", "2807"], [], {}),
    ("NET-02", "通信技术工程师", ["2801"], [], {}),
    ("NET-03", "无线通信工程师", ["2803"], [], {}),
    ("NET-04", "数据通信工程师", ["2805"], [], {}),
    ("NET-05", "光通信工程师", ["2814"], [], {}),
    ("NET-06", "有线传输工程师", ["2802"], [], {}),
    ("NET-07", "射频工程师", ["2815"], [], {}),
    ("NET-08", "核心网工程师", ["2819"], [], {}),
    ("NET-09", "通信设备工程师", ["2820"], [], {}),
    ("NET-10", "通信测试工程师", ["2816"], [], {}),
    ("NET-11", "车联网工程师", ["7119"], [], {"title_kw": ["车联网", "V2X", "T-Box"]}),
    # ---- HW 硬件与半导体 ----
    ("HW-01", "硬件工程师", ["2955", "2956", "3A00"], [], {}),
    ("HW-02", "嵌入式硬件开发", ["2919"], [], {}),
    ("HW-03", "PCB工程师", ["2964"], [], {}),
    ("HW-04", "电子工程师", ["2903", "A0MY", "2917", "2959"], ["电子工程师/技术员"], {}),
    ("HW-05", "电子元器件工程师", ["2962"], [], {}),
    ("HW-06", "电路工程师", ["2905"], ["电路工程师/技术员(模拟/数字)"], {}),
    ("HW-07", "汽车电子工程师", ["7106"], [], {}),
    ("HW-08", "集成电路IC设计", ["6701"], [], {}),
    ("HW-09", "数字前端工程师", ["6733"], [], {}),
    ("HW-10", "数字后端工程师", ["6737"], [], {}),
    ("HW-11", "模拟芯片工程师", ["6731", "A0LG"], [], {}),
    ("HW-12", "版图设计工程师", ["6722", "A0LF", "6732"], [], {}),
    ("HW-13", "IC验证工程师", ["6702"], [], {}),
    ("HW-14", "FPGA开发工程师", ["6728", "6734"], [], {}),
    ("HW-15", "芯片架构工程师", ["6727"], [], {}),
    ("HW-16", "射频芯片设计", ["6730"], [], {}),
    ("HW-17", "EDA工程师", ["6735"], [], {}),
    ("HW-18", "可测性设计工程师(DFT)", ["6736"], [], {}),
    ("HW-19", "芯片测试工程师", ["6738", "6750"], [], {}),
    ("HW-20", "封装工程师", ["6760", "6744"], [], {}),
    ("HW-21", "半导体器件工程师", ["6761"], [], {}),
    ("HW-23", "FAE现场应用工程师", ["6712"], [], {}),
    ("HW-24", "MEMS工程师", ["6729"], [], {}),
    ("HW-25", "失效分析工程师(FA)", ["6741"], [], {}),
    ("HW-26", "硬件测试工程师", ["2957"], [], {}),
    # ---- SEC 安全 ----
    ("SEC-01", "网络安全工程师", ["7906"], [], {}),
    ("SEC-02", "安全测试工程师", ["2724"], [], {"title_kw": ["渗透测试", "安全测试", "漏洞"]}),
    ("SEC-03", "密码技术应用员", ["7511"], [], {}),
    ("SEC-04", "电子数据取证分析师", ["7510"], [], {}),
    # ---- PD 产品与设计 ----
    ("PD-01", "产品经理", ["6602", "6600", "6603", "6601"], ["产品总监"], {}),
    ("PD-02", "数据产品经理", ["6611"], [], {}),
    ("PD-03", "AI产品经理", ["6612"], [], {}),
    ("PD-04", "平台产品经理", ["6615"], [], {}),
    ("PD-05", "商业化产品经理", ["6614"], [], {}),
    ("PD-06", "策略产品经理", ["6613"], [], {}),
    ("PD-07", "增长产品经理", ["6616"], [], {}),
    ("PD-08", "电商产品经理", ["6608"], [], {}),
    ("PD-09", "移动产品经理", ["6606"], [], {}),
    ("PD-10", "用户产品经理", ["6607"], [], {}),
    ("PD-11", "硬件产品经理",
     ["8600", "2954", "2813", "8604", "8602", "8603", "8601", "A0LI", "6748"],
     ["半导体产品经理/产品工程师"], {}),
    ("PD-12", "用户研究", ["6617"], [], {}),
    ("PD-13", "UI设计师", ["7412", "7403"], [], {}),
    ("PD-14", "用户体验(UE/UX)设计师", ["7404", "7402"], [], {}),
    ("PD-15", "网页设计师", ["7401"], [], {}),
    ("PD-16", "设计经理/主管", ["7423", "7424"], ["互联网设计经理/主管"], {}),
    # ---- MGT 技术管理 ----
    ("MGT-01", "技术经理", ["2604"], [], {}),
    ("MGT-02", "技术总监", ["2603", "2602"], [], {}),
    ("MGT-03", "首席技术执行官CTO", ["2611"], [], {}),
    ("MGT-04", "首席信息官CIO", ["2612"], [], {}),
    ("MGT-05", "项目经理", ["2606", "3B00", "2607", "2605", "2821"], [], {}),
    ("MGT-06", "解决方案经理", ["1D01"], [], {}),
]

# v1 纯类别节点（无 funtypes，仅组织用途）→ v2 由新类别体系替代，不迁移
DROPPED_CATEGORIES = {
    "2600": "技术管理（类别）", "0100": "后端开发（类别）", "2800": "通信技术开发与应用（类别）",
    "7900": "运维/技术支持（类别）", "7300": "人工智能（类别）", "6700": "半导体/芯片（类别）",
    "2900": "电子/电气/仪器仪表（类别）", "7400": "互联网设计（类别）", "7500": "数据（类别）",
}

# v1 岗位节点 → 剔除（低 IT 相关，v2 不收录；funtype 随之不挂，判定口径见调整方案 §2.4）
EXCLUDED = {
    # 电气/电力（传统强电域）
    "2904": "电气工程师/技术员", "7107": "电气/电器工程师", "A0L8": "电气工程师",
    # 工业自动化/制造工程
    "2908": "自动控制工程师", "2966": "PLC工程师", "2965": "SMT工程师", "2951": "电子工艺工程师",
    "2911": "电池/电源开发", "2918": "激光/光电子技术", "2963": "机器人调试工程师",
    "2969": "服务机器人应用技术员", "2970": "智能硬件装调员", "2971": "工业视觉系统运维员",
    # 新能源/汽车非电子域
    "7116": "新能源电机工程师", "7115": "新能源电控工程师", "7117": "汽车标定工程师",
    # 楼宇/安防安装
    "2125": "楼宇自动化", "A0K5": "智能大厦/综合布线/安防/弱电", "2925": "安防系统工程师",
    # 仪器/计量
    "2914": "仪器/仪表/计量分析师", "2958": "计量工程师",
    # 消费电子/维修
    "2913": "家用电器/数码产品研发", "2906": "电声/音响工程师/技术员", "2921": "变压器与磁电工程师",
    "2920": "电子电气维修工程师", "7917": "手机维修", "7918": "电脑维修", "7916": "网络维修",
    # 电信基础设施施工
    "2808": "通信电源工程师", "2818": "基站工程师", "2804": "电信交换工程师",
    # 职能/文职
    "0630": "人力资源信息系统专员", "0149": "技术文员/助理", "2608": "项目执行/协调人员",
    "2610": "项目助理", "2704": "标准化工程师",
    # 半导体制造工艺/设备（设计/验证岗保留，fab 侧剔除；HW-22 编号随之退役）
    "6723": "半导体工艺工程师", "6739": "半导体设备工程师", "6740": "工艺整合工程师(PIE)",
    "6707": "半导体技术（JD 样例定义检测为制造工艺域，随工艺域剔除）",
    # 非 IT 设计
    "7419": "美工/电商设计师", "7406": "Flash设计师", "7416": "多媒体设计",
    # 空壳冗余（语义由保留节点承接）
    "8400": "销售技术支持（空壳）", "A0JW": "前端/移动开发（空壳）", "2601": "首席CTO/CIO合并版（空壳）",
}


# ---------------- 通用工具 ----------------

def norm_part(s):
    """funtype part 规范化：小写、去空格、全角→半角、省略号→等。仅用于匹配比对。"""
    s = (s or "").strip().lower().replace(" ", "")
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0xFF08:  # （
            ch = "("
        elif code == 0xFF09:  # ）
            ch = ")"
        elif 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        out.append(ch)
    s = "".join(out)
    s = re.sub(r"\.{2,}|…{1,}", "等", s)
    return s


def load_v1():
    with open(V1_PATH, encoding="utf-8") as f:
        return json.load(f)["detail"]


def job_parts(job, v1):
    """岗位的 funtype parts（v1 sources 继承 + extra，按规范化去重，保留原文）。"""
    seen, parts = {}, []
    for code in job[2]:
        for p in v1.get(code, {}).get("funtypes") or []:
            k = norm_part(p)
            if k and k not in seen:
                seen[k] = p
                parts.append(p)
    for p in job[3]:
        k = norm_part(p)
        if k and k not in seen:
            seen[k] = p
            parts.append(p)
    return parts


def scan_part_freq(refresh=False):
    """全量扫描 funtype part 频次（缓存）。→ Counter(part → 行频次)。"""
    if not refresh and os.path.exists(PART_FREQ_CACHE):
        with open(PART_FREQ_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return Counter(d["part_freq"]), d["n_files"]
    freq, n_files = Counter(), 0
    for fn in sorted(os.listdir(common.JD_DIR)):
        if not fn.endswith(".csv"):
            continue
        n_files += 1
        with open(os.path.join(common.JD_DIR, fn), encoding="utf-8-sig", errors="replace", newline="") as fh:
            rd = csv.reader(fh)
            header = next(rd)
            i_ft = header.index("funtype")
            for row in rd:
                if len(row) <= i_ft or not row[i_ft].strip():
                    continue
                for p in common.split_parts(row[i_ft]):
                    freq[p] += 1
    os.makedirs(common.OUT_DIR, exist_ok=True)
    with open(PART_FREQ_CACHE, "w", encoding="utf-8") as f:
        json.dump({"n_files": n_files, "part_freq": dict(freq)}, f, ensure_ascii=False)
    return freq, n_files


# ---------------- --validate ----------------

def cmd_validate():
    v1 = load_v1()
    kept = [c for _, _, srcs, _, _ in JOBS for c in srcs]
    errs = []
    if len(kept) != len(set(kept)):
        dup = [c for c, n in Counter(kept).items() if n > 1]
        errs.append(f"骨架内重复引用 v1 code: {dup}")
    missing = set(v1) - set(kept) - set(EXCLUDED) - set(DROPPED_CATEGORIES)
    if missing:
        errs.append(f"未处置的 v1 节点 {len(missing)}: "
                    + ", ".join(f"{c}({v1[c]['name_zh']})" for c in sorted(missing)))
    unknown = (set(kept) | set(EXCLUDED) | set(DROPPED_CATEGORIES)) - set(v1)
    if unknown:
        errs.append(f"骨架引用了不存在的 v1 code: {sorted(unknown)}")

    names = [j[1] for j in JOBS]
    if len(names) != len(set(names)):
        errs.append(f"岗位重名: {[n for n, m in Counter(names).items() if m > 1]}")
    cat_codes = {c for c, *_ in CATEGORIES}
    for code, name, _, _, _ in JOBS:
        if code.split("-")[0] not in cat_codes:
            errs.append(f"{code} {name}: 类别前缀不在 CATEGORIES")

    print(f"v1 节点 {len(v1)} = 保留合并 {len(set(kept))} + 剔除 {len(EXCLUDED)} + 纯类别 {len(DROPPED_CATEGORIES)}"
          f" | v2 岗位 {len(JOBS)} 个 / 类别 {len(CATEGORIES)} 个")
    for e in errs:
        print(f"  [ERR] {e}")

    # funtype 覆盖
    freq, n_files = scan_part_freq()
    attached = {}
    for job in JOBS:
        for p in job_parts(job, v1):
            attached[norm_part(p)] = job[0]
    att_n = sum(n for p, n in freq.items() if norm_part(p) in attached)
    total = sum(freq.values())
    unatt = sorted(((n, p) for p, n in freq.items() if norm_part(p) not in attached), reverse=True)
    print(f"\nfuntype 覆盖（{n_files} CSV，part 频次 {total:,}）："
          f"挂载 {att_n:,}（{att_n / total:.1%}），未挂 {total - att_n:,}")
    print("未挂 part Top30（应全部属剔除域或无节点非 IT part）：")
    for n, p in unatt[:30]:
        print(f"  {n:>8,}  {p}")
    if errs:
        sys.exit(1)
    print("\n校验通过")


# ---------------- --sample ----------------

def cmd_sample(per_job=4, body_chars=900):
    v1 = load_v1()
    part2jobs = defaultdict(set)
    for job in JOBS:
        for p in job_parts(job, v1):
            part2jobs[norm_part(p)].add(job[0])
    job2meta = {j[0]: j for j in JOBS}
    samples = {j[0]: [] for j in JOBS}
    table_cnt = defaultdict(Counter)  # code → {table: n}
    todo = set(samples)

    files = sorted(f for f in os.listdir(common.JD_DIR) if f.endswith(".csv"))
    for fi, fn in enumerate(files, 1):
        if not todo:
            break
        with open(os.path.join(common.JD_DIR, fn), encoding="utf-8-sig", errors="replace", newline="") as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                ft = row.get("funtype") or ""
                codes = set()
                for p in common.split_parts(ft):
                    codes |= part2jobs.get(norm_part(p), set())
                # 标题兜底独立执行（不要求 funtype 未命中——如「安卓及鸿蒙开发工程师」
                # funtype 只挂 Android，但标题可为鸿蒙岗提供样例）
                title = (row.get("job") or "")
                for code in todo - codes:
                    for kw in job2meta[code][4].get("title_kw", []):
                        if kw.lower() in title.lower():
                            codes.add(code)
                            break
                for code in codes:
                    if code not in todo:
                        continue
                    if table_cnt[code][fn] >= 2:  # 表内多样性上限
                        continue
                    title = (row.get("job") or "").strip()
                    body = re.sub(r"\s+", " ", row.get("job_information") or "")[:body_chars]
                    if not title and not body:
                        continue
                    samples[code].append({"title": title, "body": body, "table": fn})
                    table_cnt[code][fn] += 1
                    if len(samples[code]) >= per_job:
                        todo.discard(code)
        print(f"  [{fi}/{len(files)}] {fn} 完成，剩余待采 {len(todo)}", flush=True)

    os.makedirs(common.OUT_DIR, exist_ok=True)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=1)
    empty = [job2meta[c][1] for c in samples if not samples[c]]
    print(f"\n抽样完成：{len(JOBS) - len(empty)}/{len(JOBS)} 岗位有样例（每岗 ≤{per_job} 条）→ {SAMPLES_PATH}")
    if empty:
        print(f"零样例岗位（定义将仅由名称+大类生成）: {'、'.join(empty)}")


# ---------------- --define ----------------

DEF_PROMPT = """你是信息技术岗位知识库构建助手。基于岗位名称、所属大类、同大类其他岗位清单和若干条真实招聘JD摘录，为该岗位生成标准化知识条目。

要求：
- name_en：标准英文职位名
- definition：职责定义 2-3 句（共 60-120 字），覆盖核心职责、典型技术栈/工具、关键产出
- keywords：12-20 个用于在招聘文本中识别该岗位的区分性关键词（中英文均可；优先具体技术/工具/领域词，避免与其他岗位重合的泛词）
- boundary：一句话说明与最易混淆岗位的边界
严格只输出一个 JSON 对象，不要任何其他文字：
{{"name_en":"...","definition":"...","keywords":["..."],"boundary":"..."}}

目标岗位：{name}（大类：{cat_name}）
同大类其他岗位（关键词避开与其重合）：{siblings}
{samples_block}"""


def _parse_obj(text):
    text = (text or "").strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def call_llm_raw(api_key, model, prompt, max_tokens=1500, timeout=120, retries=3):
    """调用 DeepSeek 返回**原文**（classify_jobs.call_api 固定提取 JSON 数组，不适用对象输出）。"""
    import urllib.request
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 0,
                       "thinking": {"type": "disabled"}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return resp["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last}")


def common_cj():
    sys.path.insert(0, os.path.abspath(os.path.join(common.HERE, "..", "job_classify_51job")))
    import classify_jobs
    return classify_jobs


def cmd_define(model, limit=0):
    with open(SAMPLES_PATH, encoding="utf-8") as f:
        samples = json.load(f)
    done = {}
    if os.path.exists(DEF_CACHE):
        with open(DEF_CACHE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    done[d["code"]] = d
    pending = [j for j in JOBS if not done.get(j[0], {}).get("definition")]  # 失败记录重跑
    if limit:
        pending = pending[:limit]
    print(f"待生成定义 {len(pending)} 岗位（已完成 {len(done)}，model={model}）", flush=True)
    if not pending:
        return
    cj = common_cj()
    api_key = cj.load_api_key("")
    if not api_key:
        print("错误：未找到 API key", file=sys.stderr)
        sys.exit(1)
    cat_name = {c[0]: c[1] for c in CATEGORIES}
    by_cat = defaultdict(list)
    for j in JOBS:
        by_cat[j[0].split("-")[0]].append(j[1])

    for i, job in enumerate(pending, 1):
        code, name = job[0], job[1]
        sibs = "、".join(n for n in by_cat[code.split("-")[0]] if n != name)
        ss = samples.get(code) or []
        if ss:
            block = "真实 JD 摘录：\n" + "\n".join(
                f"{k}. {s['title'] or '(无标题)'} | {s['body'][:450]}" for k, s in enumerate(ss, 1))
        else:
            block = "（暂无 JD 样例，请依据岗位名称与行业常识生成）"
        prompt = (DEF_PROMPT.replace("{name}", name).replace("{cat_name}", cat_name[code.split("-")[0]])
                  .replace("{siblings}", sibs).replace("{samples_block}", block))
        rec = None
        for attempt in range(3):
            try:
                obj = _parse_obj(call_llm_raw(api_key, model, prompt))
                if not isinstance(obj, dict) or not obj.get("definition"):
                    raise ValueError("输出缺 definition")
                rec = {"code": code, "name_zh": name,
                       "name_en": str(obj.get("name_en", ""))[:80],
                       "definition": str(obj.get("definition", "")).strip(),
                       "keywords": [str(k) for k in (obj.get("keywords") or [])][:24],
                       "boundary": str(obj.get("boundary", "")).strip(),
                       "model": model, "n_samples": len(ss)}
                break
            except Exception as e:
                print(f"  [retry {attempt + 1}] {name}: {e}", flush=True)
                time.sleep(3 * (attempt + 1))
        if rec is None:
            rec = {"code": code, "name_zh": name, "name_en": "", "definition": "",
                   "keywords": [], "boundary": "", "model": model,
                   "n_samples": len(ss), "error": "3 次尝试失败"}
        done[code] = rec
        with open(DEF_CACHE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  [{i}/{len(pending)}] {code} {name} ✓ ({len(rec['keywords'])} 关键词)", flush=True)
    bad = [d["name_zh"] for d in done.values() if not d.get("definition")]
    if bad:
        print(f"\n完成，但 {len(bad)} 个岗位生成失败（重跑 --define 续作）: {'、'.join(bad)}")
    else:
        print(f"\n全部 {len(done)} 岗位定义生成完毕")


# ---------------- --finalize ----------------

def cmd_finalize():
    v1 = load_v1()
    with open(SAMPLES_PATH, encoding="utf-8") as f:
        samples = json.load(f)
    done = {}
    with open(DEF_CACHE, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                done[d["code"]] = d
    missing_def = [j[0] for j in JOBS if j[0] not in done or not done[j[0]].get("definition")]
    if missing_def:
        print(f"[ERR] {len(missing_def)} 岗位缺定义，先完成 --define: {missing_def}")
        sys.exit(1)

    freq, _ = scan_part_freq()
    detail = {}
    for code, name, srcs, extras, misc in JOBS:
        d = done[code]
        parts = job_parts((code, name, srcs, extras, misc), v1)
        v1_names = [v1[c]["name_zh"] for c in srcs if c in v1]
        detail[code] = {
            "code": code,
            "category": code.split("-")[0],
            "name_zh": name,
            "name_en": d.get("name_en") or v1.get(srcs[0], {}).get("name_en", ""),
            "definition": d["definition"],
            "keywords": d["keywords"],
            "boundary": d.get("boundary", ""),
            "funtypes": parts,
            "source_codes": srcs,
            "source_names": v1_names,
            "hits": sum(freq.get(p, 0) for p in parts),
            "n_samples": len(samples.get(code) or []),
        }

    kept = sorted({c for _, _, srcs, _, _ in JOBS for c in srcs})
    out = {
        "system_name": "信息技术岗位体系",
        "version": "2.0",
        "date": time.strftime("%Y-%m-%d"),
        "source": ("51job funtype 体系 v1.1（jobs0806.json，255 节点）重构：类别/岗位两级职责分离、"
                   "相近岗位合并、剔除低 IT 相关岗位；定义与关键词由 LLM 阅读数据集真实 JD 生成"),
        "meta": {
            "n_categories": len(CATEGORIES),
            "n_jobs": len(JOBS),
            "design": ("一级类别为纯组织维度（不挂 funtypes、不进 prompt 标签/图节点）；二级岗位为唯一实体。"
                       "岗位归类不依赖 funtype——由逐 JD 关键词词库+LLM 判定（keywords 即快路词库，"
                       "引擎待建，模式同 classify_stacks.py）；funtypes 保留用于溯源与 JD 抽样"),
            "from_v1": {"kept_merged": len(kept), "excluded_non_it": len(EXCLUDED),
                        "dropped_categories": len(DROPPED_CATEGORIES),
                        "excluded_detail": EXCLUDED, "dropped_category_detail": DROPPED_CATEGORIES},
            "generation": {"definition_model": done[JOBS[0][0]].get("model"),
                           "samples_per_job": 4, "def_cache": "codes/jd_annotate/output/jobs_def_cache.jsonl"},
        },
        "categories": [{"code": c, "name_zh": n, "name_en": e, "description": d}
                       for c, n, e, d in CATEGORIES],
        "detail": detail,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_kw = sum(len(d["keywords"]) for d in detail.values())
    print(f"已写出 {OUT_PATH}")
    print(f"类别 {len(CATEGORIES)} / 岗位 {len(JOBS)} / 关键词 {n_kw} 个（均值 {n_kw / len(JOBS):.0f}）")
    for c in out["categories"]:
        js = [d for d in detail.values() if d["category"] == c["code"]]
        print(f"  {c['code']} {c['name_zh']}: {len(js)} 岗")


# ---------------- --doc ----------------

def cmd_doc():
    """由 jobs_v2.json 渲染可读介绍文档（仅岗位与描述；体系变更后重跑即同步）。"""
    with open(OUT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    by_cat = defaultdict(list)
    for d in data["detail"].values():
        by_cat[d["category"]].append(d)
    lines = []
    w = lines.append
    w("# 信息技术岗位分类体系介绍")
    w("")
    w(f"> 版本 {data['version']} · {data['date']} · 共 **{data['meta']['n_categories']} 大类 / "
      f"{data['meta']['n_jobs']} 个岗位**　|　数据文件 `classify/Jobs/jobs_v2.json`")
    w("> 本文档由 `codes/jd_annotate/build_jobs.py --doc` 生成，与体系文件保持同步。")
    w("")
    w("体系采用「一级类别（纯组织维度）→ 二级岗位（唯一实体）」两级结构：类别仅用于归类导航，"
      "岗位是归类的目标实体。岗位归类按 JD 内容判定（关键词词库 + LLM），不依赖 51job funtype；"
      "各岗位的定义由 LLM 阅读数据集中的真实招聘 JD 样例生成。")
    w("")
    w("## 总览")
    w("")
    w("| # | 类别 | 代码 | 岗位数 | 覆盖范围 |")
    w("|---|------|------|--------|----------|")
    for i, c in enumerate(data["categories"], 1):
        w(f"| {i} | {c['name_zh']} | {c['code']} | {len(by_cat[c['code']])} | {c['description']} |")
    w("")
    zh_num = "一二三四五六七八九十"
    for i, c in enumerate(data["categories"], 1):
        w(f"## {zh_num[i - 1]}、{c['name_zh']}（{c['code']}）· {len(by_cat[c['code']])} 个岗位")
        w("")
        w(f"> {c['description']}")
        w("")
        for d in by_cat[c["code"]]:
            w(f"### {d['name_zh']}（{d['name_en']}）")
            w("")
            w(d["definition"])
            if d.get("boundary"):
                w("")
                w(f"**边界**：{d['boundary']}")
            w("")
    os.makedirs(os.path.dirname(INTRO_PATH), exist_ok=True)
    with open(INTRO_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    print(f"已生成 {INTRO_PATH}（{data['meta']['n_jobs']} 岗位 / {data['meta']['n_categories']} 大类）")


def main():
    ap = argparse.ArgumentParser(description="岗位分类体系 v2.0 构建（骨架 + LLM 定义）")
    ap.add_argument("--validate", action="store_true", help="校验骨架处置完整性与 funtype 覆盖")
    ap.add_argument("--sample", action="store_true", help="扫描 JD 数据集为每岗抽样真实 JD")
    ap.add_argument("--define", action="store_true", help="LLM 逐岗生成定义/关键词（断点续跑）")
    ap.add_argument("--finalize", action="store_true", help="合并骨架+定义 → classify/Jobs/jobs_v2.json")
    ap.add_argument("--doc", action="store_true", help="由 jobs_v2.json 生成 introduction/ 可读介绍文档")
    ap.add_argument("--limit", type=int, default=0, help="--define 最多处理岗位数（测试用）")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    args = ap.parse_args()
    if not (args.validate or args.sample or args.define or args.finalize or args.doc):
        ap.print_help()
        return
    if args.validate:
        cmd_validate()
    if args.sample:
        cmd_sample()
    if args.define:
        cmd_define(args.model, args.limit)
    if args.finalize:
        cmd_finalize()
    if args.doc:
        cmd_doc()


if __name__ == "__main__":
    main()
