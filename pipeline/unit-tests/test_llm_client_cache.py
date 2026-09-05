# -*- coding: utf-8 -*-
"""extractor LLM 基础设施单测：KeyRing 多账号轮转（线程安全）、LLMClient 稳健
JSON 提取与批级容错（单批失败不中断整窗）、句级结果缓存（规范化键 + 持久化 +
旧格式兼容）。全部离线（不触网、不读真实 api-key）。"""
import threading

import ut

ut.setup("extractor")
ut.isolate()

import llm
from cache import ResultCache, _normalize
from llm_client import LLMClient


def test_keyring_round_robin():
    """轮转序确定；单 key 恒返回该 key。"""
    ring = llm.KeyRing(["k1", "k2", "k3"])
    assert [ring.next() for _ in range(5)] == ["k1", "k2", "k3", "k1", "k2"]
    assert len(llm.KeyRing(["only"])) == 1
    assert llm.KeyRing(["only"]).next() == "only"


def test_keyring_thread_safety():
    """并发下轮转不丢号：n 线程各取 m 次 → 总取次数与返回集合计数一致。"""
    keys = [f"k{i}" for i in range(4)]
    ring = llm.KeyRing(keys)
    got = []
    lock = threading.Lock()

    def pull():
        for _ in range(50):
            k = ring.next()
            with lock:
                got.append(k)

    ts = [threading.Thread(target=pull) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(got) == 200
    from collections import Counter
    cnt = Counter(got)
    assert all(cnt[k] == 50 for k in keys), cnt      # 均匀且无丢失


def test_extract_json_array_tolerant():
    """模型输出容错：裸数组 / 前后带说明文字 / markdown 围栏均能取出。"""
    assert LLMClient._extract_json_array('[{"code": "S-01"}]') == [{"code": "S-01"}]
    assert LLMClient._extract_json_array('结果如下：\n[1, 2] 以上。') == [1, 2]
    try:
        LLMClient._extract_json_array("没有数组")
        raise SystemExit("应抛 ValueError")
    except ValueError:
        pass


def test_gather_batch_fault_tolerance():
    """批级容错：单批失败（重试耗尽）不中断整体，其余批结果保序合并。"""
    client = LLMClient(api_key="offline-key")        # 显式 key：不读真实密钥文件
    calls = []

    def run_batch(batch):
        calls.append(batch)
        if "bad" in batch[0]:
            raise RuntimeError("重试耗尽")
        return {u: [{"code": "S-01", "skillpoints": []}] for u in batch}

    out = client._gather([["good-1", "good-2"], ["bad-1"], ["good-3"]], run_batch)
    assert out == {"good-1": [{"code": "S-01", "skillpoints": []}],
                   "good-2": [{"code": "S-01", "skillpoints": []}],
                   "good-3": [{"code": "S-01", "skillpoints": []}]}
    assert len(calls) == 3


def test_sentence_cache_roundtrip(tmp_path):
    """缓存键规范化（空白/大小写/标点）+ JSONL 持久化 + 命中统计。"""
    assert _normalize("熟悉 Python，熟练使用。") == _normalize("熟悉python熟练使用")
    c = ResultCache("skill", cache_dir=str(tmp_path))
    assert c.get("从未见过的句子") is None and c.misses == 1
    c.set("熟悉 Python，熟练使用。", [{"code": "S-01", "skillpoints": ["Python"]}])
    assert c.get("熟悉python熟练使用") == [{"code": "S-01", "skillpoints": ["Python"]}]
    assert c.hits == 1 and c.size() == 1

    # 重新加载（模拟下次运行）：持久化 + 旧格式（codes 无技能点）兼容
    c2 = ResultCache("skill", cache_dir=str(tmp_path))
    assert c2.get("熟悉python熟练使用") is not None
    import json as _json
    with open(c2.path, "a", encoding="utf-8") as f:
        f.write(_json.dumps({"key": "legacy", "codes": ["S-09"]}, ensure_ascii=False) + "\n")
    c3 = ResultCache("skill", cache_dir=str(tmp_path))
    assert c3.get("legacy") == [{"code": "S-09", "skillpoints": []}]
