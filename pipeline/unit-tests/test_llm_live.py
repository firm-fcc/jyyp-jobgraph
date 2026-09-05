# -*- coding: utf-8 -*-
"""LLM 真调用冒烟用例（小批量实付调用，经用户授权）。

与其余 175 个离线用例的分工：离线用例以桩验证协议与算法；本文件对**真实 API
端点**小批量验证同一套契约（KeyRing 轮转 / 稳健 JSON 解析 / 句级分类 /
merged 抽取），每次运行共 3 次调用、合计约数千 token（成本 ≈ 几分钱）。

运行条件：codes/api-key.txt 存在（与生产同一密钥文件）；无密钥环境下这 3 例
自动跳过（pytest skip），不影响离线套件结果。实测观察写入
.live-observations.json，由 run_tests.py 汇入 test-cases.csv 的"结果说明"列。
"""
import json
import os
import sys

import pytest

import ut

ut.setup("extractor")
ut.isolate()

from llm import call_llm
from llm_client import LLMClient
from extractor import Extractor
from taxonomy import load_skills, load_tasks

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(ut.ROOT, "codes", "api-key.txt")
OBS_PATH = os.path.join(HERE, ".live-observations.json")

requires_key = pytest.mark.skipif(
    not os.path.exists(KEY_FILE), reason="需要 LLM API key（codes/api-key.txt）")


@pytest.fixture(autouse=True)
def _restore_module_state():
    """用例后恢复跨包冲突名：生产 llm_client._post 在轮转模式下惰性 `import llm`
    会连带把 extractor 侧 config 绑进 sys.modules；弹出后由后续文件的
    setup/isolate 按各自包路径重新解析（保持既有套件语义）。"""
    yield
    sys.modules.pop("config", None)
    sys.modules.pop("llm", None)


def _record(case, note):
    """实测观察落盘（run_tests.py 汇入 CSV 结果说明列）。"""
    obs = {}
    if os.path.exists(OBS_PATH):
        try:
            obs = json.load(open(OBS_PATH, encoding="utf-8"))
        except (OSError, ValueError):
            obs = {}
    obs[case] = note
    with open(OBS_PATH, "w", encoding="utf-8") as f:
        json.dump(obs, f, ensure_ascii=False, indent=1)


@requires_key
def test_live_call_llm_json_contract():
    """真端点 JSON 契约：单次小调用经 KeyRing 轮转，返回解析为 dict 且语义正确。"""
    r = call_llm('只输出 JSON：{"ok": true}', parse_json=True, max_tokens=200)
    assert isinstance(r, dict) and r.get("ok") is True
    _record("test_live_call_llm_json_contract",
            f"真实端点单次调用成功，实测返回 {json.dumps(r, ensure_ascii=False)[:60]}；"
            f"经 KeyRing 轮转（api-key.txt 首个可用 key）")


@requires_key
def test_live_classify_sentences_skill():
    """真端点句级分类：生产 classify_sentences 链路（提示词+49 技能体系）小批量实测。"""
    tax = load_skills()
    valid = {l["code"] for l in tax.labels}
    sentences = ["熟悉Python开发与数据分析", "负责机器学习模型训练与调优"]
    out = LLMClient().classify_sentences(sentences, tax)
    n_nonempty = 0
    observed = {}
    for s in sentences:
        assert s in out, f"缺句：{s}"
        matches = out[s]
        assert isinstance(matches, list)
        for m in matches:
            assert m["code"] in valid, f"非法技能码：{m['code']}"
            assert "skillpoints" in m
        observed[s] = [m["code"] for m in matches]
        n_nonempty += bool(matches)
    assert n_nonempty >= 1, "至少一句应得到非空分类"
    _record("test_live_classify_sentences_skill",
            "实测分类：" + "; ".join(f"{s}→{cs or '(空)'}" for s, cs in observed.items()))


@requires_key
def test_live_merged_extraction():
    """真端点 merged 抽取：Stage B 生产路径（一句一次：技能+技能点+任务）小批量实测。"""
    skills, tasks = load_skills(), load_tasks()
    valid_sk = {l["code"] for l in skills.labels}
    valid_tk = {l["code"] for l in tasks.labels}
    ext = Extractor(mode="merged", use_cache=False)
    units = ["熟悉Python后端开发", "负责MySQL数据库性能优化"]
    results, agg = ext._classify_units(units, taxonomy=None)   # merged 模式忽略 taxonomy 形参
    observed = {}
    for u in units:
        m = results[u]
        assert set(m) >= {"skills", "tasks", "overlays"}, f"结构缺键：{sorted(m)}"
        for sk in m["skills"]:
            assert sk["code"] in valid_sk, f"非法技能码：{sk['code']}"
        for c in m["tasks"]:
            assert c in valid_tk, f"非法任务码：{c}"
        observed[u] = (sorted(sk["code"] for sk in m["skills"]), list(m["tasks"]))
    assert sum(agg["skill_counts"].values()) + sum(agg["task_counts"].values()) >= 1, \
        "至少应抽到一项技能或任务"
    _record("test_live_merged_extraction",
            "实测 merged 抽取：" + "; ".join(
                f"{u}→技能{sk or '无'}/任务{tk or '无'}" for u, (sk, tk) in observed.items()))
