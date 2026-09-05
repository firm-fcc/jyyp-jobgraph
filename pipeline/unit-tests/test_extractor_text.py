# -*- coding: utf-8 -*-
"""extractor 文本处理与信号层单测：JD 分句（括号保护 / 长度过滤 / 保序去重）、
体系加载（技能/任务/岗位三表标签化）、论文候选信号校验（超长名降级不丢弃 /
非法信号拦截）、新闻提及映射（norm 归一 + 体系精确命中零 LLM）。离线。"""
import ut

ut.setup("extractor")
ut.isolate()

import text_split
from text_split import split_sentences, dedupe_preserve_order
from taxonomy import Taxonomy, load_skills, load_tasks, load_jobs
from signal_extractor import fit_name, _validate_signal, Candidate
from mention_mapper import norm, _build_lookup
from news_filter import _settings, _title_guide


# ---------------- JD 分句 ----------------

def test_split_sentences_boundaries_and_paren():
    """按句末标点/换行切分，括号内容保护不内切。"""
    text = "负责后端开发，精通Java。要求：熟悉MySQL（包括索引优化）；\n具备分布式经验！"
    sents = split_sentences(text)
    assert 0 < len(sents) <= 5
    joined = " ".join(sents)
    assert "Java" in joined and "MySQL" in joined
    # 句内逗号不切分（一条职责一个子句）
    assert all("，" not in x or len(x) > 1 for x in sents)


def test_split_sentences_length_filter():
    """最短长度过滤、空文本容错、保序去重。"""
    text = "短。" * 3 + "这是一条足够长的岗位职责描述内容用于通过最小长度阈值过滤校验。"
    sents = split_sentences(text)
    assert all(len(x) >= text_split.config.SENTENCE_MIN_LEN for x in sents)
    # 空文本容错
    assert split_sentences("") == [] and split_sentences(None) == []
    assert dedupe_preserve_order(["a", "b", "a", "c"]) == ["a", "b", "c"]


# ---------------- 体系加载 ----------------

def test_taxonomy_label_loading():
    """三体系加载：mode 正确、标签非空；Taxonomy 直接构造的双向索引。"""
    for loader, mode in ((load_skills, "skill"), (load_tasks, "task"), (load_jobs, "job")):
        tax = loader()
        assert isinstance(tax, Taxonomy) and tax.mode == mode
        assert tax.labels and all(x.get("code") and x.get("name_zh") for x in tax.labels)
    # Taxonomy 直接构造：code↔名称 双向索引与标签文本
    t = Taxonomy([{"code": "S-01", "name_zh": "机器学习"}], mode="skill", name="测试体系")
    assert t.label_text() == "S-01:机器学习"
    assert t.code_to_name["S-01"] == "机器学习" and t.name_to_code["机器学习"] == "S-01"
    assert len(t) == 1


# ---------------- 论文候选信号校验 ----------------

class _Paper:
    """论文/新闻记录统一接口（doc_id/pub_date/title/body）。"""
    doc_id = "2401.00001"
    pub_date = "2024-01-01"
    title = "A Paper on Something"
    abstract = "We propose a new method."
    body = "We propose a new method for federated training across institutions."


def _cand(**kw):
    base = dict(index=0, record=_Paper(), kind="new_skill", name_zh="联邦学习",
                name_en="Federated Learning", definition="跨机构联合建模",
                rationale="多篇论文提出", evidence=["e1"], confidence="high")
    base.update(kw)
    return Candidate(**base)


def test_fit_name_degrades_not_drops():
    """超长信号名降级：保尾部核心词截断，不丢弃。"""
    long_name = "基于深度强化学习的智能电网调度与优化方法研究"
    fitted = fit_name(long_name, max_chars=12)
    assert len(fitted) <= 12 and fitted          # 降级保留尾部核心词，不丢弃
    assert fit_name("联邦学习") == "联邦学习"


def test_validate_signal_rules():
    """信号校验防线：缺名/空证据/paper_index 越界拒收；非法置信度降级 low 不丢弃；超长名经入口降级。"""
    papers = [_Paper()]
    ok = {"index": 0, "paper_index": 0, "kind": "new_skill", "name_zh": "联邦学习",
          "name_en": "FL", "definition": "d", "rationale": "r",
          "evidence": ["文中提出联邦学习框架"], "confidence": "high"}
    s = _validate_signal(ok, papers)
    assert s is not None and s.name_zh == "联邦学习" and s.confidence == "high"
    # 缺名 / 空证据 / paper_index 越界 → None（防编造防线）
    for bad in (dict(ok, name_zh=""), dict(ok, evidence=[]), dict(ok, paper_index=7)):
        assert _validate_signal(bad, papers) is None
    # 非法置信度不丢弃 → 降级 low（信号不因字段瑕疵流失）
    degraded = _validate_signal(dict(ok, confidence="ultimate"), papers)
    assert degraded is not None and degraded.confidence == "low"
    # 超长名经 _validate_signal 入口降级（保尾部核心词，不丢弃信号）
    long_name = "基于深度强化学习的智能电网调度与优化方法研究方法论体系"
    s2 = _validate_signal(dict(ok, name_zh=long_name), papers)
    assert s2 is not None and len(s2.name_zh) <= 20


# ---------------- 新闻提及映射 ----------------

def test_mention_norm_and_lookup():
    """提及归一（去空白标点+小写）与体系索引：中英文精确命中零 LLM。"""
    assert norm("Machine Learning") == norm("machine learning")
    labels = {"skills": [{"code": "S-01", "name_zh": "机器学习", "name_en": "Machine Learning"}],
              "tasks": [{"code": "T-01", "name_zh": "模型训练"}],
              "jobs":  [{"code": "DEV-01", "name_zh": "算法工程师"}]}
    lookup = _build_lookup(labels)
    assert lookup["skills"][norm("机器学习")] == "S-01"
    assert lookup["tasks"][norm("模型训练")] == "T-01"
    assert lookup["jobs"][norm("算法工程师")] == "DEV-01"
    # name_en 也入索引（英文提及直接命中，零 LLM）
    assert lookup["skills"][norm("Machine Learning")] == "S-01"
    assert norm("无关名称") not in lookup["skills"]


# ---------------- 新闻过滤配置 ----------------

def test_news_filter_settings_fallback():
    """逐级下钻读取：键缺失/文件异常 → 默认值兜底（不抛错）。"""
    assert _settings("news_filter", "not_exist_key", default=17) == 17
    assert _settings("not_exist", "x", "y", default="ok") == "ok"


def test_news_filter_title_guide():
    """过滤导语窗口：正文规范化截断为指引文本。"""
    guide = _title_guide(_Paper())
    assert isinstance(guide, str) and guide                      # 非空指引文本
