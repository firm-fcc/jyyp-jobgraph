# -*- coding: utf-8 -*-
"""LLM 调用封装补测：LLMClient._post 成功/限速重试/不可重试直抛与 token 记账
（mock urlopen），builder 侧 call_llm 成功与截断升级路径。零网络。"""
import io
import json
import urllib.error

import pytest

import ut

ut.setup("builder")           # 先绑 builder 侧 llm（call_llm 与 extractor 同构、独立实现）
ut.isolate()
import llm as bllm            # noqa: E402  builder/llm.py
import sys as _sys
_sys.modules.pop("config", None)       # 仅弹 config：llm_client 运行期按 extractor 解析
ut.setup("builder", "extractor")   # 其余（LLMClient/llm_client）按 extractor 解析


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok(content, finish="stop", tokens=10):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"total_tokens": tokens}}


def _err(code):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(b""))


def test_client_post_success_and_tokens(monkeypatch):
    """输入：mock urlopen 返回带 usage 的成功载荷（total_tokens=77）。期望输出：解析出数组；total_tokens=77 call_count=1（成本核算口径）。"""
    from llm_client import LLMClient
    import llm_client as lc
    client = LLMClient(api_key="offline")
    monkeypatch.setattr(lc.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(_ok('[{"code": "S-01"}]', tokens=77)))
    out = client._post("p")
    assert out == [{"code": "S-01"}]
    assert client.total_tokens == 77 and client.call_count == 1   # token 记账（成本核算口径）


def test_client_post_429_retry_then_ok(monkeypatch):
    """输入：首请求 429、次请求成功。期望输出：第二次成功返回 []，共 2 次请求。"""
    from llm_client import LLMClient
    import llm_client as lc
    state = {"n": 0}

    def fake(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise _err(429)                                     # 限速：退避后重试
        return _Resp(_ok("[]"))

    monkeypatch.setattr(lc.urllib.request, "urlopen", fake)
    client = LLMClient(api_key="offline")
    assert client._post("p") == [] and state["n"] == 2


def test_client_post_non_retryable_raises(monkeypatch):
    """输入：mock urlopen 恒抛 404。期望输出：HTTPError 直抛。"""
    from llm_client import LLMClient
    import llm_client as lc
    monkeypatch.setattr(lc.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_err(404)))
    with pytest.raises(urllib.error.HTTPError):
        LLMClient(api_key="offline")._post("p")


def test_builder_call_llm_success_and_length_upgrade(monkeypatch):
    """输入：builder 侧 call_llm：首响应 finish_reason=length、次响应成功。期望输出：返回解析对象；第二次请求 max_tokens=首次×2（封顶 MAX_TOKENS_CAP）；确证覆盖 builder 实现。"""
    assert "codes/builder" in bllm.__file__.replace("\\", "/")   # 确证覆盖的是 builder 实现
    calls = []

    def fake(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body["max_tokens"])
        if len(calls) == 1:
            return _Resp(_ok('{"a":', finish="length"))
        return _Resp(_ok('{"a": 1}'))

    monkeypatch.setattr(bllm.urllib.request, "urlopen", fake)
    assert bllm.call_llm("p", api_key="k") == {"a": 1}
    assert calls[1] == min(calls[0] * 2, bllm.config.MAX_TOKENS_CAP)
