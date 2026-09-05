# -*- coding: utf-8 -*-
"""Stage A 岗位归类门（jd_annotate 包）单测：词库匹配器语义、文本指纹、
A 门 collect（泛词岗路由 / 指纹去重 / 预抽样过滤 / 严格门 / 排除表）、
行迭代器（BOM / 嵌套换行 / limit）。零 LLM（misses 只收集不调用）。"""
import csv
import os

import ut

ut.setup("jd_annotate")

import common
from classify_job import (collect, is_excluded_job_title, AMBIGUOUS_JOB_NAMES,
                          load_jobs_v2)
from classify_stacks import iter_jd_rows


# ---------------- common：匹配器与指纹 ----------------

_SYN_TAX = {
    "X-01": {"keywords": ["Java", "数据分析"]},
    "X-02": {"keywords": ["go", "微信支付"]},
    "X-03": {"keywords": ["C++"]},
}


def test_matcher_ascii_boundary():
    """ascii 关键词按字母数字边界：java ⊄ javascript、go ⊄ golang；大小写不敏感。"""
    m = common.StackMatchers(_SYN_TAX)
    assert [c for _, c in m.scan("需要Java开发经验")] == ["X-01"]
    assert m.scan("熟悉javascript框架") == []            # 边界拦截
    assert m.scan("GOLANG后端开发") == []                # go ⊂ golang
    assert [c for _, c in m.scan("会Go语言和C++")] == ["X-02", "X-03"]


def test_matcher_chinese_and_order():
    """中文关键词按子串；多命中按首现位置排序（跨岗共享词的排序抢占防线）。"""
    m = common.StackMatchers(_SYN_TAX)
    hits = m.scan("微信支付对账，兼做数据分析")
    assert [c for _, c in hits] == ["X-02", "X-01"]      # 首现位置序
    assert [c for _, c in m.scan("数据分析师")] == ["X-01"]


def test_rule_stacks_tiers():
    """词库快路三态：标题命中 tier1（不扫正文）、正文命中 tier2、未命中 tier0。"""
    m = common.StackMatchers(_SYN_TAX)
    stacks, tier = common.rule_stacks(m, "数据分析岗", "其他内容")
    assert stacks == ["X-01"] and tier == 1             # 标题命中 tier1（不扫正文）
    stacks, tier = common.rule_stacks(m, "招聘专员", "要求熟悉数据分析工具")
    assert stacks == ["X-01"] and tier == 2             # 正文命中 tier2
    assert common.rule_stacks(m, "招聘专员", "负责行政事务") == ([], 0)


def test_jd_text_key_and_parts():
    """指纹：空白归一化后同文同键（重复发布/模板抄袭合键）；funtype 拆分口径。"""
    a = common.jd_text_key("Java工程师", "职责：\n\n开发  后端")
    b = common.jd_text_key("Java工程师", "职责： 开发 后端")
    assert a == b
    assert a != common.jd_text_key("Go工程师", "职责：开发 后端")
    assert common.split_parts("技术类 or 产品类") == ["技术类", "产品类"]
    assert common.split_parts("") == []
    assert common.is_excluded_title("电镀工艺工程师") and not common.is_excluded_title("Java工程师")


# ---------------- classify_stacks：行迭代器 ----------------

def _write_jd_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job", "job_information"])
        for title, text in rows:
            w.writerow([title, text])


def test_iter_rows_bom_and_embedded_newline(tmp_path):
    """utf-8-sig（BOM）读取；job_information 字段内的真实换行（带引号）不拆行。

    这是"wc -l 行数虚高 ~14×"问题的口径防线：记录数必须以 CSV 解析为准。
    """
    p = tmp_path / "mini.csv"
    body_with_nl = "职责一：负责后端开发\n职责二：熟悉MySQL数据库"   # CSV 引号字段内换行
    _write_jd_csv(str(p), [("Java工程师", body_with_nl), ("测试工程师", "编写测试用例")])
    rows = list(iter_jd_rows(str(p)))
    assert len(rows) == 2                                 # 嵌套换行不产生额外记录
    assert rows[0][1] == "Java工程师" and "\n" in rows[0][2]
    # limit 生效
    assert len(list(iter_jd_rows(str(p), limit=1))) == 1
    # 不存在的文件跳过不抛错；缺 job_information 列容错为空串
    p2 = tmp_path / "no_col.csv"
    with open(p2, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([["job"], ["数据分析师"]])
    rows2 = list(iter_jd_rows(str(p2)))
    assert rows2[0][2] == ""


# ---------------- classify_job：A 门 collect ----------------

def test_collect_gate_and_dedup(tmp_path):
    """四路信号一窗走通：名称层直收 / 泛词名改送 LLM / 排除表 non_it / 指纹去重。"""
    p = tmp_path / "gate.csv"
    rows = [
        # 1) 非泛词岗位名命中标题 → tier1 直收（名称层）
        ("Java开发工程师", "负责服务端开发"),
        # 2) 泛词岗名 + 正文无软件技术内容 → 不直收，改送 LLM（正文定域）
        ("项目经理", "负责厂房建设进度跟进与施工方日常对接"),
        # 3) 排除表（物理制造域）+ 词库未命中 → 规则级 non_it
        ("电镀工艺员", "负责电镀线日常工艺参数记录"),
        # 4) 与 1) 同文（空白差异）→ 指纹去重
        ("Java开发工程师", "负责服务端开发"),
    ]
    _write_jd_csv(str(p), rows)
    misses, st, classified = collect(str(p))

    assert st["rows"] == 4 and st["unique_all"] == 3 and st["unique"] == 3, st
    assert st["presampled_out"] == 0
    direct = [k for k, v in classified.items() if v.get("tier") == 1 and not v.get("non_it")]
    assert len(direct) == 1 and classified[direct[0]]["jobs"] == ["DEV-01"], classified
    assert st["ambig_name_to_llm"] == 1 and len(misses) == 1, (st, list(misses))
    assert "正文" not in misses  # 占位防呆：misses 键为指纹
    non_it = [v for v in classified.values() if v.get("non_it")]
    assert len(non_it) == 1 and non_it[0]["jobs"] == [] and non_it[0]["tier"] == 0


def test_collect_presample_filter(tmp_path):
    """Stage S0 预抽样：非选中键在指纹计算后跳过（规则与 LLM 都不见）。"""
    p = tmp_path / "pre.csv"
    _write_jd_csv(str(p), [("Java开发工程师", "负责服务端开发"),
                           ("数据分析师", "负责经营分析报表")])
    all_keys = {common.jd_text_key(t, x) for t, x in
                [("Java开发工程师", "负责服务端开发"), ("数据分析师", "负责经营分析报表")]}
    keep = {next(k for k in all_keys)}                     # 只保留一个键
    misses, st, classified = collect(str(p), presample=keep)
    assert st["unique_all"] == 2 and st["unique"] + st["presampled_out"] == 2
    assert st["presampled_out"] == 1
    assert len(classified) == 1                            # 未选键不产生任何归类


def test_collect_strict_gate(tmp_path):
    """严格门（基图管线口径）：关键词命中（tier1 关键词 / tier2 正文）改送 LLM 复核，
    仅岗位名层直收。"""
    p = tmp_path / "strict.csv"
    _write_jd_csv(str(p), [("Java开发工程师", "负责服务端开发"),    # 名称层，直收
                           ("诚聘英才", "要求熟悉Java编程")])     # 仅正文关键词
    misses_lax, st_lax, cls_lax = collect(str(p))
    misses_strict, st_strict, cls_strict = collect(str(p), strict=True)
    assert len(cls_lax) == 2 and st_lax["miss"] == 0
    # 严格门：正文关键词命中不再直收 → 进 misses
    assert len(cls_strict) == 1 and len(misses_strict) == 1
    assert st_strict["strict"] is True


def test_ambiguous_names_registered():
    """泛词名清单须是体系内真实存在的 name_zh（路由前提）。"""
    detail, _ = load_jobs_v2()
    names = {d["name_zh"] for d in detail.values()}
    assert AMBIGUOUS_JOB_NAMES <= names
    assert "Java开发工程师" not in AMBIGUOUS_JOB_NAMES
    assert is_excluded_job_title("维修电工") and not is_excluded_job_title("软件工程师")
