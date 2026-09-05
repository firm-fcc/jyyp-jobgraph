# -*- coding: utf-8 -*-
"""JD 行级标注引擎（annotate_jd）单测：级别解析四级回退（work_year 列 > 正文年限 >
正文应届 > 标题词 > funtype 兜底）、确定性技术名词层（词边界 / 版本折叠 / 位置
抑制 / 停用词）、技术栈三层解析（词库 + LLM 缓存容缺）。零 LLM。"""
import json
import os

import ut

ut.setup("jd_annotate")

import common
import annotate_jd as aj


# ---------------- 级别解析 ----------------

def test_parse_work_year():
    """结构化 work_year 列 → (级别, 年限)：区间取下界、在校生/应届 L0、不限 0、空 None。"""
    assert aj.parse_work_year("3-5年") == (None, 3)
    assert aj.parse_work_year("10年以上") == (None, 10)
    assert aj.parse_work_year("在校生/应届生") == ("L0", 0)
    assert aj.parse_work_year("经验不限") == (None, 0)
    assert aj.parse_work_year("") == (None, None)


def test_parse_text_years():
    """正文年限要求 → int：取最高线索、中文数字识别、cap 15、「无经验」计 0。"""
    assert aj.parse_text_years("要求5年以上开发经验") == 5
    assert aj.parse_text_years("具有三年相关经验者优先") == 3          # 中文数字
    assert aj.parse_text_years("无经验亦可，公司提供培训") == 0
    assert aj.parse_text_years("要求20年从业经历") == 15               # cap 15
    assert aj.parse_text_years("") is None


def test_years_to_level_thresholds():
    """年限 → 级别阈值表：≤2 L1、≤4 L2、≤9 L3、≥10 L4。"""
    assert [aj.years_to_level(n) for n in (1, 2, 3, 4, 5, 9, 10)] == \
        ["L1", "L1", "L2", "L2", "L3", "L3", "L4"]


def test_resolve_level_priority():
    """级别四级回退优先级：work_year 列 > 正文年限 > 正文应届 > 标题词 > funtype 兜底，判不出空串。"""
    # 结构化列最高优
    assert aj.resolve_level("3-5年", "高级工程师", "要求8年经验") == ("L2", "work_year")
    # 列缺失 → 正文年限（覆盖标题词）
    assert aj.resolve_level("", "高级工程师", "要求3年以上经验") == ("L2", "text")
    # 正文应届 → L0
    assert aj.resolve_level("", "软件工程师", "欢迎应届毕业生投递") == ("L0", "text")
    # 标题词
    assert aj.resolve_level("", "资深Java开发工程师", "职责描述") == ("L3", "title")
    # funtype 兜底（source 标记区分展示）
    assert aj.resolve_level("", "开发工程师", "职责描述", funtype="高级软件工程师") == ("L3", "funtype")
    # 判不出
    assert aj.resolve_level("", "开发工程师", "职责描述") == ("", "")


# ---------------- 确定性技术名词层 ----------------

def test_extract_tech_mentions_boundaries():
    """英文词边界 + 中文子串；停用词在词池构建期已剔除（优化/测试等泛概念不入池）。"""
    m = aj.extract_tech_mentions("Java开发工程师", "熟悉JavaScript前端与MySQL调优")
    assert m == {"Java", "JavaScript", "MySQL"}
    # 别名折叠：golang 是 Go 的注册别名 → 命中 Go
    assert "Go" in aj.extract_tech_mentions("后端", "使用golang开发微服务")
    # 边界：go ⊄ google（短名边界拦截，Go 不误报）
    assert "Go" not in aj.extract_tech_mentions("运维", "使用Google云平台")
    # 停用词：泛概念词不产生命中
    m2 = aj.extract_tech_mentions("系统工程师", "负责系统优化、测试与应用部署")
    assert m2 == set(), m2


def test_extract_tech_mentions_version_and_suppression():
    """输入：标题+正文："维护MySQL8主从集群"、"用Vue3重构组件库"、"基于Spring Boot…"、"负责微信小程序…"。期望输出：MySQL/Vue 命中（版本号折叠）；Spring Boot、微信小程序命中且 Spring、微信不重复出现（位置抑制）。"""
    # 版本折叠：MySQL8 / Vue3 命中母项
    assert "MySQL" in aj.extract_tech_mentions("DBA", "维护MySQL8主从集群")
    assert "Vue" in aj.extract_tech_mentions("前端", "用Vue3重构组件库")
    # 位置抑制：Spring Boot 命中时 Spring（被完全覆盖）不重复出现
    m = aj.extract_tech_mentions("后端", "基于Spring Boot的微服务")
    assert "Spring Boot" in m and "Spring" not in m
    # 中文位置抑制：微信小程序 ⊃ 微信
    m = aj.extract_tech_mentions("小程序开发", "负责微信小程序商城模块")
    assert "微信小程序" in m and "微信" not in m


def test_extract_tech_mentions_short_names():
    """短名（≤2 字符）边界附加 +/#/.：C++ 文本不误命中 C。"""
    m = aj.extract_tech_mentions("客户端", "精通C++与面向对象设计")
    assert "C++" in m
    # 独立出现的 C（词边界完整）应命中池中 canon
    m2 = aj.extract_tech_mentions("嵌入式", "熟悉C语言单片机开发")
    assert "C" in m2


# ---------------- 技术栈三层解析（词库 + LLM 缓存容缺） ----------------

def test_stack_annotator_tiers(tmp_path, monkeypatch):
    """三层解析：标题词库 tier1 / 正文词库 tier2 / LLM 缓存 tier3 / 无信号 tier0。"""
    # LLM 缓存指到临时文件：tier3 只读缓存，不触网
    cache = tmp_path / "stack_cache.jsonl"
    key = common.jd_text_key("综合工程师", "参与公司信息化项目建设")
    with open(cache, "w", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "stacks": ["TS-TEST"], "tier": 3}) + "\n")
    monkeypatch.setattr(common, "JD_STACK_CACHE", str(cache))
    ann = aj.StackAnnotator()

    s1, t1 = ann.resolve("数据分析工程师", "职责描述")          # tier1 标题词库
    assert t1 == 1 and s1
    s2, t2 = ann.resolve("专员", "要求掌握数据分析方法")        # tier2 正文词库
    assert t2 == 2 and s2
    s3, t3 = ann.resolve("综合工程师", "参与公司信息化项目建设")  # tier3 LLM 缓存
    assert t3 == 3 and s3 == ["TS-TEST"]
    s4, t4 = ann.resolve("综合专员", "处理日常行政事务")          # tier0 无信号
    assert t4 == 0 and s4 == []


def test_stack_annotator_cache_tolerant_missing(tmp_path, monkeypatch):
    """缓存文件不存在 → 空表容缺，词库层照常（生产冷启动路径）。"""
    monkeypatch.setattr(common, "JD_STACK_CACHE", str(tmp_path / "none.jsonl"))
    ann = aj.StackAnnotator()
    assert ann.cache == {}
    s, t = ann.resolve("数据分析工程师", "职责")
    assert t == 1 and s
