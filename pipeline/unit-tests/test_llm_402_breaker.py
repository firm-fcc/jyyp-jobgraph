# -*- coding: utf-8 -*-
"""402 熔断机制单测（2026-09-02 用户裁定：资源性故障不得降级保信号）。

2025-06 窗实证：key 余额耗尽时 map_signals 的保守降级（映射失败→整批 keep-new）
绕过全部守门，34 条未审实体出生。三层防线：
① LLM 层：单 key 402 先轮转其他 key；启用 key 全部 402 → ResourceExhaustedError；
② 调用方：降级 except 前放行熔断类型（中止运行）；
③ map_signals：熔断时不得 force-keep。
mock 零真实网络。
"""
import json
import os
import sys
import types
import urllib.error

import ut

ut.setup("graph", "builder", "extractor")
from delta_store import DeltaStore                             # noqa: E402,F401
ut.setup("extractor")

import llm                                                      # noqa: E402
import taxonomy_mapper as tm                                   # noqa: E402
from signal_extractor import Candidate                         # noqa: E402


def _http_402():
    return urllib.error.HTTPError("url", 402, "Payment Required", {}, None)


class _Resp:
    """真实上下文管理器响应（dunder 只在类上查找，SimpleNamespace 实例属性无效）。"""

    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_response():
    body = {"choices": [{"message": {"content": '{"decisions": []}'},
                         "finish_reason": "stop"}]}
    return _Resp(json.dumps(body).encode())


def _setup_ring(monkeypatch, keys):
    """绕过探测、注入指定 key 的 KeyRing（无网络）。"""
    monkeypatch.setattr(llm, "_RING", llm.KeyRing(keys))
    return llm.KeyRing(keys)


def test_all_keys_402_circuit_breaks(monkeypatch):
    """全部启用 key 402 → ResourceExhaustedError（不再降级为普通失败）。"""
    _setup_ring(monkeypatch, ["kA", "kB"])
    monkeypatch.setattr(llm.config, "RETRIES", 3)
    calls = []

    def fake_urlopen(req, timeout=None):
        auth = req.headers.get("Authorization", "")
        calls.append(auth[-4:])
        raise _http_402()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    try:
        llm.call_llm("测试")
        raise AssertionError("应熔断")
    except llm.ResourceExhaustedError as e:
        assert "402" in str(e) and "全部启用 key 不可用" in str(e)
    assert len(calls) == 2, "两个 key 各试一次后熔断"


def test_partial_402_rotates_to_healthy_key(monkeypatch):
    """部分 key 402 → 轮转到健康 key 成功返回（不熔断）。"""
    _setup_ring(monkeypatch, ["kA", "kB", "kC"])

    def fake_urlopen(req, timeout=None):
        auth = req.headers.get("Authorization", "")
        if auth.endswith("kA") or auth.endswith("kB"):
            raise _http_402()
        return _ok_response()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    out = llm.call_llm("测试", parse_json=False)
    assert out == '{"decisions": []}'


def test_map_signals_propagates_breaker(monkeypatch):
    """map_signals 主映射熔断 → 向上抛出（不得整批 force-keep 绕过守门）。"""

    def boom(prompt, parse_json=True, max_tokens=None, api_key=None):
        raise llm.ResourceExhaustedError("全部启用 key 不可用（402 余额耗尽）——熔断")

    monkeypatch.setattr(tm, "call_llm", boom)
    rec = types.SimpleNamespace(doc_id="d0", pub_date="2025-06-01")
    cands = [Candidate(0, rec, "new_task", "某候选任务", "", "", "依据", [], "high")]
    try:
        tm.map_signals(cands, {"tasks": [], "skills": [], "jobs": []}, [])
        raise AssertionError("熔断应向上传播")
    except llm.ResourceExhaustedError:
        pass
