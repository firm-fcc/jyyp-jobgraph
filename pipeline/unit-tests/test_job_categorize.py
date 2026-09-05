# -*- coding: utf-8 -*-
"""转正后类别归纳（job_categorize）自测：扫描空类别 / 注入建议的写回流程
（备份、version bump、日志）/ suggest-only 不写 / 无待归类 no-op。

运行：cd codes/graph && python fixtures/test_job_categorize.py
（临时基准副本 + 注入 _suggest，零 LLM，不触碰正式产物。）
"""
import json
import os
import shutil
import sys
import tempfile

import ut

ut.setup("graph", "builder")
ut.isolate()

from job_categorize import categorize, find_uncategorized, build_prompt  # noqa: E402
import graph_config as gconfig                     # noqa: E402
import config                                       # noqa: E402  builder config


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def _mk_fixture(tmp):
    """真实 jobs_v2 副本 + 追加一个空类别 GJ-999（模拟转正落盘后待归类状态）。"""
    jobs_path = os.path.join(tmp, "jobs_v2.json")
    shutil.copy(gconfig.BASE_NODE_FILES["jobs"], jobs_path)
    data = json.load(open(jobs_path, encoding="utf-8"))
    data["detail"]["GJ-999"] = {"code": "GJ-999", "category": "", "name_zh": "测试新岗位",
                                "name_en": "", "definition": "设计提示词与大模型工作流的工程师。",
                                "keywords": [], "boundary": "", "funtypes": ["测试新岗位"],
                                "graduated": "2026-08-31"}
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jobs_path, data


def test_find(tmp):
    """待归类查找：GJ- 无类别的岗位入清单（含式：空类别不排他）。"""
    print("== find_uncategorized：只找空/非法 category ==")
    jobs_path, data = _mk_fixture(tmp)
    found = dict(find_uncategorized(jobs_path))
    _assert("GJ-999" in found, "GJ-999 待归类（含式——真实基准转正的 GJ 可能为空类别，不排他）")
    # 真实基准：待归类数 = 副本待归类数 - 1（GJ-999 是本夹具追加的）——只断不劣化
    real = find_uncategorized()
    _assert(len(real) == len(found) - 1, f"正式基准待归类与副本差 1（{len(real)} vs {len(found)-1}）")


def test_flow(tmp):
    """全流程：建议被接受→category 写入→备份目录与内容→归类日志→二次运行收敛。"""
    print("== 写回流程：备份/version/日志/写值 ==")
    jobs_path, data = _mk_fixture(tmp)
    v_before = data["version"]
    log_path = os.path.join(tmp, "promotion_log.md")

    calls = []

    def fake_suggest(code, job, d):
        calls.append(code)
        return {"category": "AID", "confidence": 0.85, "reason": "提示词工程属 AI 工程化",
                "runner_up": "DEV"}

    rep = categorize(jobs_path=jobs_path, assume_yes=True, backup_root=tmp,
                     log_path=log_path, _suggest=fake_suggest)
    _assert(rep["confirmed"].get("GJ-999") == "AID", "GJ-999 建议被接受（含式）")
    _assert("GJ-999" in calls, "GJ-999 触发建议调用")
    after = json.load(open(jobs_path, encoding="utf-8"))
    _assert(after["detail"]["GJ-999"]["category"] == "AID", "category 已写入")
    _assert(str(after["version"]) != str(v_before), f"version 已 bump（{v_before} → {after['version']}）")
    _assert(os.path.basename(rep["backup_dir"]).startswith("categorize-"), "备份目录 categorize-*")
    _assert(os.path.exists(os.path.join(rep["backup_dir"], "jobs_v2.json")), "备份含基准文件")
    # 除 category/version 外其余字段不动；日志只写注入路径（不污染正式 promotion_log）
    for k, v in data["detail"]["GJ-999"].items():
        if k != "category":
            _assert(after["detail"]["GJ-999"][k] == v, f"字段 {k} 未被改动")
    _assert(os.path.exists(log_path) and "转正后类别归纳" in open(log_path, encoding="utf-8").read(),
            "归类日志写入注入路径")
    # 再次运行：无待归类 → no-op，不再调建议函数
    rep2 = categorize(jobs_path=jobs_path, assume_yes=True, backup_root=tmp,
                      _suggest=fake_suggest)
    _assert(rep2["n_todo"] == 0 and calls.count("GJ-999") == 1, "二次运行收敛（GJ-999 不重复）")


def test_suggest_only(tmp):
    """suggest-only 模式：只出建议不写基准文件。"""
    print("== suggest-only：只打印不写 ==")
    jobs_path, data = _mk_fixture(tmp)
    raw_before = open(jobs_path, "rb").read()
    rep = categorize(jobs_path=jobs_path, suggest_only=True, backup_root=tmp,
                     _suggest=lambda c, j, d: {"category": "DEV", "confidence": 0.5,
                                               "reason": "r", "runner_up": ""})
    _assert(rep["confirmed"] == {} and open(jobs_path, "rb").read() == raw_before,
            "suggest-only 不写基准")


def test_prompt(tmp):
    """提示词组装：含九大类别体系与各岗清单。"""
    print("== prompt 构造 ==")
    jobs_path, data = _mk_fixture(tmp)
    p = build_prompt("GJ-999", data["detail"]["GJ-999"], data)
    _assert("一级类别体系" in p and "AID" in p and "测试新岗位" in p,
            "prompt 含类别体系与新岗位定义")
    _assert(p.count("现有岗位：") == len(data["categories"]), "9 类各带现有岗位清单")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        test_find(tmp)
        test_flow(tmp)
        test_suggest_only(tmp)
        test_prompt(tmp)
    print("\n全部通过。")
