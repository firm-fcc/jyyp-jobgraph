# -*- coding: utf-8 -*-
"""JD/论文 ΔG 信号通道单测（离线）：JD 信号提取（上下文构建 / 校验规则 / 批次解析 /
LLM 失败容错）、提及映射（精确命中零 LLM / 未命中走 LLM 组映射 / 未知 type 丢弃）、
论文提及识别器（mode 校验 / 提单元构建）。call_llm 一律 mock。"""
import ut

ut.setup("extractor")
ut.isolate()

import jd_extractor as jx
import mention_mapper as mm
from mention_mapper import map_mentions, norm
from paper_mention import PaperMentionExtractor


class _JDRecord:
    def __init__(self, doc_id, title, body):
        self.doc_id = doc_id
        self.title = title
        self.funtype = "技术类"
        self.pub_date = "2026-05-01"
        self.body = body


def _papers():
    return [_JDRecord("J1", "算法工程师", "要求熟悉联邦学习与差分隐私，负责隐私计算平台开发。"),
            _JDRecord("J2", "数据工程师", "负责数据仓库建模与调度。")]


# ---------------- JD 信号提取 ----------------

def test_build_extract_prompt_context():
    """提示词组装：叠层清单注入 + 逐 JD 上下文（jd_index/title/funtype/body 截断）。"""
    prompt = jx.build_extract_prompt(_papers(), overlay_labels="技能A、任务B")
    assert "技能A、任务B" in prompt
    assert "jd_index: 0" in prompt and "title: 算法工程师" in prompt
    assert "jd_index: 1" in prompt


def test_fit_name_and_validate():
    """JD 侧信号校验：定义与证据必填（双防线）、kind 枚举、提及 type/长度校验、超长名降级。"""
    assert jx._fit_name("短名") == "短名"
    long = "基于深度强化学习的智能电网调度与优化方法研究"
    assert len(jx._fit_name(long)) <= jx.MAX_NAME_CHARS
    ok = {"kind": "new_skill", "name_zh": "联邦学习", "definition": "跨机构联合建模",
          "evidence": ["文中提出"], "confidence": "high"}
    c = jx._validate_signal(ok, _JDRecord("J1", "t", "b"), 0)
    assert c is not None and c.name_zh == "联邦学习"
    # 定义与证据必填（防编造双防线）
    for bad in (dict(ok, definition=""), dict(ok, evidence=[]), dict(ok, kind="bad-kind")):
        assert jx._validate_signal(bad, _papers()[0], 0) is None
    # 提及校验：type 枚举 + 名称长度
    rec0 = _papers()[0]
    assert jx._validate_mention({"type": "skill", "name": "机器学习", "evidence": ["e"]}, rec0) is not None
    assert jx._validate_mention({"type": "unknown", "name": "机器学习"}, rec0) is None
    assert jx._validate_mention({"type": "skill", "name": "x"}, rec0) is None


def test_extract_jd_signals_batch(monkeypatch):
    """批次解析：按 jd_index 对齐记录、非法条目与越界 index 跳过、候选与提及分流。"""
    records = _papers()
    payload = {"jd_signals": [
        {"jd_index": 0, "new_signals": [
            {"kind": "new_skill", "name_zh": "联邦学习", "definition": "联合建模",
             "evidence": ["要求熟悉联邦学习"], "confidence": "high"},
            {"kind": "bad", "name_zh": "非法", "definition": "d", "evidence": ["e"]},
        ], "mentions": [{"type": "skill", "name": "机器学习", "evidence": ["e1"]}]},
        {"jd_index": 9, "new_signals": []},                   # 越界条目跳过
    ]}
    monkeypatch.setattr(jx, "call_llm", lambda *a, **k: payload)
    cands, mentions = jx.extract_jd_signals(records)
    assert len(cands) == 1 and cands[0].name_zh == "联邦学习"
    assert mentions == {"J1": [{"type": "skill", "name": "机器学习", "evidence": ["e1"]}]}


def test_extract_jd_signals_llm_failure(monkeypatch):
    """批次 LLM 失败容错：返回空不中断。"""
    def boom(*a, **k):
        raise RuntimeError("LLM 调用失败")

    monkeypatch.setattr(jx, "call_llm", boom)
    assert jx.extract_jd_signals(_papers()) == ([], {})


# ---------------- 提及映射 ----------------

_LABELS = {"skills": [{"code": "S-01", "name_zh": "机器学习", "name_en": "Machine Learning"}],
           "tasks": [{"code": "T-01", "name_zh": "模型训练"}],
           "jobs": [{"code": "DEV-01", "name_zh": "算法工程师"}]}


def test_map_mentions_exact_hit_no_llm(monkeypatch):
    """体系内精确命中走索引零 LLM（断言式 mock 验证未被调用）。"""
    monkeypatch.setattr(mm, "_llm_map_group",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("精确命中不应调 LLM")))
    mentions = [{"type": "skill", "name": "机器学习", "evidence": []},
                {"type": "task", "name": "模型训练", "evidence": []},
                {"type": "job", "name": "算法工程师", "evidence": []}]
    assert map_mentions(mentions, _LABELS) == {"机器学习": "S-01", "模型训练": "T-01",
                                               "算法工程师": "DEV-01"}


def test_map_mentions_llm_group_and_drop(monkeypatch):
    """未命中按 type 分组送 LLM 组映射；未知 type 与空名丢弃。"""
    captured = {}

    def fake_group(group, tax, labels, api_key, max_tokens, logger):
        captured[tax] = [m["name"] for m in group]
        return {"联邦学习": "S-07"}

    monkeypatch.setattr(mm, "_llm_map_group", fake_group)
    mentions = [{"type": "skill", "name": "联邦学习", "evidence": []},
                {"type": "unknown-type", "name": "未知物", "evidence": []},
                {"type": "skill", "name": "", "evidence": []}]
    out = map_mentions(mentions, _LABELS)
    assert out == {"联邦学习": "S-07"}                        # 未知 type / 空名丢弃
    assert captured == {"skills": ["联邦学习"]}              # 未命中按 type 分组送 LLM
    assert map_mentions([], _LABELS) == {}


# ---------------- 论文提及识别器 ----------------

class _Paper:
    title = "Federated Learning Survey"
    keywords = ["联邦学习", "隐私保护"]
    abstract = "本文综述联邦学习的最新进展与隐私保护技术。讨论了多方安全计算的应用前景与挑战。"
    evidence_sentences = ["实验部分比较了多种差分隐私方案。"]


def test_paper_mention_units_and_mode_guard():
    """论文提单元构建（标题/关键词/摘要句/证据句）与非法 mode 拒绝。"""
    ext = PaperMentionExtractor(mode="skill", llm_client=object(), use_cache=False)
    units = ext._paper_units(_Paper())
    assert any(u.startswith("标题：") for u in units)
    assert any(u.startswith("关键词：") and "联邦学习" in u for u in units)
    assert any("差分隐私" in u for u in units)                    # 摘要句 + 证据句均入单元
    try:
        PaperMentionExtractor(mode="unknown")
        raise SystemExit("应抛 ValueError")
    except ValueError:
        pass
