# -*- coding: utf-8 -*-
"""extractor/llm 单测（离线）：稳健 JSON 提取（围栏/杂讯/括号平衡）、call_llm 成功
路径、finish_reason=length 的 max_tokens 升级重试、402 余额熔断（单 key）与
402 轮转（多 key 先换再断）、非重试 HTTP 错误直抛。urlopen 一律 mock。"""
import io
import json
import urllib.error

import pytest

import ut

ut.setup("extractor")
ut.isolate()

import llm
from llm import KeyRing, ResourceExhaustedError, call_llm, _extract_json


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"total_tokens": 10}}


def _http_402():
    return urllib.error.HTTPError("http://api", 402, "Payment Required", {}, io.BytesIO(b""))


# ---------------- JSON 提取 ----------------

def test_extract_json_tolerant():
    """JSON 提取容错：裸/围栏/前后杂讯/数组优先；无 JSON 报错。"""
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('说明文字 [1, 2] 结尾') == [1, 2]      # 数组优先
    assert _extract_json('前缀 {"k": "v"} 后缀') == {"k": "v"}
    with pytest.raises(ValueError):
        _extract_json("完全不是 JSON")


# ---------------- 成功路径 ----------------

def test_call_llm_success(monkeypatch):
    """输入：mock urlopen 返回 choices 载荷；parse_json 两种模式。期望输出：True 模式 → {"ok": true}；False 模式 → 原文；请求体 messages 携带提示词。"""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp(_ok('{"ok": true}'))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    assert call_llm("提示词", parse_json=True, api_key="sk-test") == {"ok": True}
    assert call_llm("提示词", parse_json=False, api_key="sk-test") == '{"ok": true}'
    assert sent[0]["messages"][0]["content"] == "提示词"


def test_call_llm_length_upgrade(monkeypatch):
    """finish_reason=length → max_tokens 翻倍重试（推理模型输出预算防线）。"""
    calls = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body["max_tokens"])
        if len(calls) == 1:
            return _Resp(_ok('{"ok":', finish="length"))       # 截断
        return _Resp(_ok('{"ok": true}'))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    assert call_llm("p", api_key="k") == {"ok": True}
    assert calls[1] == min(calls[0] * 2, llm.config.MAX_TOKENS_CAP)


# ---------------- 402 熔断与轮转 ----------------

def test_call_llm_402_breaker_single_key(monkeypatch):
    """显式单 key 402 → 全部启用 key 不可用，立即熔断（资源性故障不降级）。"""
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_http_402()))
    with pytest.raises(ResourceExhaustedError):
        call_llm("p", api_key="only-key")


def test_call_llm_402_rotates_then_breaks(monkeypatch):
    """轮转模式：先换下一个 key；环上全部 402 才熔断。"""
    ring = KeyRing(["k1", "k2"])
    monkeypatch.setattr(llm, "key_ring", lambda: ring)
    used = []

    def fake_urlopen(req, timeout=None):
        used.append(req.headers.get("Authorization"))
        raise _http_402()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ResourceExhaustedError):
        call_llm("p")
    assert len(set(used)) == 2                             # 两个 key 都试过


def test_call_llm_402_then_success(monkeypatch):
    """输入：轮转环 2 key，首请求 402、次请求成功。期望输出：返回 {"ok": 1}（换 key 后成功）。"""
    ring = KeyRing(["k1", "k2"])
    monkeypatch.setattr(llm, "key_ring", lambda: ring)
    state = {"n": 0}

    def fake_urlopen(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise _http_402()
        return _Resp(_ok('{"ok": 1}'))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    assert call_llm("p") == {"ok": 1}


def test_call_llm_non_retryable_http_error(monkeypatch):
    """不可重试 HTTP 错（404）直抛不重试。"""
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))

    def fake_urlopen(req, timeout=None):
        raise err

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        call_llm("p", api_key="k")
