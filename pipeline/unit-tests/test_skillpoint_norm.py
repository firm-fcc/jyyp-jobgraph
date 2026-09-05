# -*- coding: utf-8 -*-
"""skillpoint 三层归一 fixture：L1 折叠 / L2 别名 / L3 LLM 首见（mock）/ 去重保序。零 LLM。"""
import json
import os
import sys
import tempfile

import ut

ut.setup("graph", "builder")
ut.isolate()

import skillpoint_norm as sn


class _TmpNormalizer(sn.SkillpointNormalizer):
    """别名缓存指到临时目录，避免污染真实缓存文件。"""

    def __init__(self, llm_post=None):
        self._tmpdir = tempfile.mkdtemp()
        sn.ALIAS_CACHE_PATH = os.path.join(self._tmpdir, "alias_cache.jsonl")
        super().__init__(llm_post=llm_post, use_cache=True)


def test_l1_folding():
    """输入：["Mybatis","MyBatis","HTML 5","HTML5","NodeJS","zookeeper"]。期望输出：归一为 MyBatis、HTML（含 retired 重映射）、Node.js、ZooKeeper。"""
    norm = _TmpNormalizer()
    m = norm.resolve_batch(["Mybatis", "MyBatis", "HTML 5", "NodeJS", "zookeeper", "HTML5"])
    assert m["Mybatis"][0] == "MyBatis" and m["MyBatis"][0] == "MyBatis"
    assert m["HTML 5"][0] == "HTML"           # HTML5 已退役并入 HTML（retired 重映射）
    assert m["HTML5"][0] == "HTML"
    assert m["NodeJS"][0] == "Node.js"        # 注册表别名 + norm_key
    assert m["zookeeper"][0] == "ZooKeeper"


def test_l2_alias_semantic():
    """输入：["C语言","Golang","K8s","单片机","MCU","WIFI","IIC","以太网"]。期望输出：C、Go、Kubernetes、单片机（MCU 并入）、Wi-Fi、I2C、以太网。"""
    norm = _TmpNormalizer()
    m = norm.resolve_batch(["C语言", "Golang", "K8s", "单片机", "MCU", "WIFI", "IIC", "以太网"])
    assert m["C语言"][0] == "C"
    assert m["Golang"][0] == "Go"
    assert m["K8s"][0] == "Kubernetes"
    assert m["单片机"][0] == "单片机" and m["MCU"][0] == "单片机"
    assert m["WIFI"][0] == "Wi-Fi"
    assert m["IIC"][0] == "I2C"
    assert m["以太网"][0] == "以太网"


def test_distinct_not_merged():
    """独立名不得误并：同前缀不同代际各自保留。"""
    # 硬口径：相近但不同的技术不得合并（L1/L2 层就不该合并）
    norm = _TmpNormalizer()
    m = norm.resolve_batch(["Spring", "Spring Boot", "Spring Cloud", "C", "C++", "C#",
                            "Angular", "AngularJS", "MyBatis", "MyBatis-Plus"])
    canon = {v[0] for v in m.values()}
    assert canon == {"Spring", "Spring Boot", "Spring Cloud", "C", "C++", "C#",
                     "Angular", "AngularJS", "MyBatis", "MyBatis-Plus"}
    # 版本号并入母项（v2 口径）
    assert m["Angular"][0] == "Angular" and m["AngularJS"][0] == "AngularJS"


def test_l3_llm_first_seen():
    """L3 首见：LLM 拆分（主名+类别）入缓存，再见零调用；缓存行可持久化。"""
    calls = []

    def mock_post(prompt):
        calls.append(prompt)
        # 阿里巴巴中间件 → RocketMQ（merge）；自研名词 → new
        return [{"name": "RocketMQ中间件", "action": "merge", "canonical": "RocketMQ", "category": "中间件"},
                {"name": "TDSQL", "action": "new", "canonical": "TDSQL", "category": "数据库"}]

    norm = _TmpNormalizer(llm_post=mock_post)
    m = norm.resolve_batch(["RocketMQ中间件", "TDSQL"])
    assert m["RocketMQ中间件"][0] == "RocketMQ" and m["RocketMQ中间件"][1] == "中间件"
    assert m["TDSQL"][0] == "TDSQL" and m["TDSQL"][1] == "数据库"
    # 缓存生效：第二次解析不再调 LLM
    m2 = norm.resolve_batch(["RocketMQ中间件"])
    assert m2["RocketMQ中间件"][0] == "RocketMQ" and len(calls) == 1
    # 缓存文件已写
    lines = [json.loads(l) for l in open(sn.ALIAS_CACHE_PATH, encoding="utf-8")]
    assert any(l["name"] == "TDSQL" for l in lines)


def test_normalize_skillpoint_map_dedup():
    """整映射归一去重：别名/变体折叠后按键去重保序。"""
    norm = _TmpNormalizer()
    out = norm.normalize_skillpoint_map({"T-SW-01": ["MyBatis", "Mybatis", "Spring MVC"],
                                         "T-DA-02": ["MySQL"]})
    assert out == {"T-SW-01": ["MyBatis", "Spring MVC"], "T-DA-02": ["MySQL"]}


def test_expansion_and_retired():
    """展开集（C→C/C++/Linux）与退役重映射（HTML5→HTML）。"""
    norm = _TmpNormalizer()
    # C/C++ 书写惯例 → 展开为 C 与 C++ 各计一次（expansions）
    out = norm.normalize_skillpoint_map({"T-SW-01": ["C/C++", "Linux"],
                                         "T-SW-02": ["ES6", "CSS3"]})
    assert out["T-SW-01"] == ["C", "C++", "Linux"]
    assert out["T-SW-02"] == ["JavaScript", "CSS"]   # 版本号并入母项（retired）
