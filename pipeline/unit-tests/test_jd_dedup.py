# -*- coding: utf-8 -*-
"""jd_dedup 单测：simhash 确定性与近重敏感性、分块候选完备性、Jaccard 复核、
星型聚类保最早、变体产物读写与消费方兼容。"""
import os
import sys

import ut

ut.setup("graph", "builder")
ut.isolate()
import jd_dedup as dd  # noqa: E402


def _doc(i, text, opentime=None):
    ot = opentime or ("2022-06-%02d 00:00:00" % (i + 1))
    return {"key": f"k{i}", "jobid": f"J{i:04d}", "opentime": ot, "text": text}


BASE = ("岗位职责：1、负责公司后台服务的设计与开发，保障系统稳定运行；"
        "2、参与需求分析和技术方案评审，输出设计文档。"
        "任职要求：1、本科及以上学历，计算机相关专业；"
        "2、熟练掌握 Java、Spring、MySQL 等主流技术栈，熟悉分布式系统与缓存机制；"
        "3、三年以上服务端开发经验，具备良好的沟通能力和团队合作精神。")


# ---------------- simhash ----------------
def test_simhash_deterministic():
    """simhash 确定性：同文同指纹（64 位整数域）。"""
    a, b = dd.simhash(dd.gram_hashes(BASE)), dd.simhash(dd.gram_hashes(BASE))
    assert a == b and isinstance(a, int) and 0 <= a < (1 << 64)


def test_simhash_near_dup_sensitivity():
    """换皮微改（公司名/年限/一处措辞）→ 海明距小；不同正文 → 海明距大。"""
    variant = BASE.replace("三年以上", "五年以上").replace("计算机相关专业", "软件工程专业")
    unrelated = ("岗位职责：负责门店日常运营管理与人员排班，处理顾客投诉，"
                 "完成月度销售报表。任职要求：高中以上学历，有零售经验者优先。")
    fp0 = dd.simhash(dd.gram_hashes(BASE))
    fpv = dd.simhash(dd.gram_hashes(variant))
    fpu = dd.simhash(dd.gram_hashes(unrelated))
    assert dd.hamming(fp0, fpv) <= 6          # 换皮变体：海明距小（阈值内）
    assert dd.hamming(fp0, fpu) > 10          # 无关正文：海明距大


def test_jaccard_confirm_reject():
    """Jaccard 复核：换皮变体 ≥0.95 通过、不同正文 <0.1 拒绝。"""
    variant = BASE.replace("三年以上", "五年以上")
    jv = len(set(dd.gram_hashes(BASE)) & set(dd.gram_hashes(variant))) / \
        len(set(dd.gram_hashes(BASE)) | set(dd.gram_hashes(variant)))
    ju = len(set(dd.gram_hashes(BASE)) & set(dd.gram_hashes("完全无关的一段文字" * 10))) / \
        len(set(dd.gram_hashes(BASE)) | set(dd.gram_hashes("完全无关的一段文字" * 10)))
    assert jv >= 0.95
    assert ju < 0.1


# ---------------- 聚类 ----------------
def test_cluster_keep_earliest():
    """换皮簇保最早发布；不同模板各自成簇。"""
    variant = BASE.replace("三年", "五年")             # 单点换皮（Jaccard≈0.96）
    other = ("岗位职责：1、负责数据平台的ETL开发与维护，支撑业务报表与数据分析；"
             "2、优化数仓模型与调度任务，保障数据质量。"
             "任职要求：1、本科及以上学历，数学、统计或计算机相关专业；"
             "2、熟练使用 SQL，熟悉 Hive/Spark 数仓工具与维度建模方法；"
             "3、两年以上数据处理经验，有大型数仓项目经历者优先。")
    docs = [
        _doc(0, BASE, "2022-06-01 00:00:00"),          # 最早 → 代表
        _doc(1, variant, "2022-06-05 00:00:00"),       # 换皮变体 → 并入 0
        _doc(2, other, "2022-06-10 00:00:00"),         # 另一模板 → 独立簇
        _doc(3, "完全不同的岗位描述。" * 15, "2022-06-15 00:00:00"),     # 独立簇
    ]
    parent, st = dd.cluster_near_dups(docs)

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    assert find(1) == 0                        # 变体并入最早发布
    assert find(0) == 0 and find(2) == 2 and find(3) == 3
    assert st["n_confirmed"] >= 1


def test_blocks_pigeonhole():
    """海明距 ≤3 的两指纹必共享至少一块 16 位。"""
    fp = dd.simhash(dd.gram_hashes(BASE))
    fp2 = fp ^ 0b101                            # 海明距 2
    b1, b2 = set(dd.blocks_of(fp)), set(dd.blocks_of(fp2))
    assert b1 & b2


# ---------------- 产物读写（消费方兼容） ----------------
def test_load_variants_absent():
    """产物缺失消费方容错：返回空 dict 不抛错（向后兼容）。"""
    assert dd.load_variants("2099-01") == {}   # 产物缺失 → 空（向后兼容）


def test_artifact_roundtrip():
    """写产物 → load_variants 读回（JD_DERIVED_DIR 临时重定向）。"""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        real = dd.gconfig.JD_DERIVED_DIR
        dd.gconfig.JD_DERIVED_DIR = td
        try:
            rec = {"variants": {"k2": "k1", "k3": "k1"}}
            with open(os.path.join(td, "2022-06.dedup.json"), "w", encoding="utf-8") as f:
                json.dump(rec, f)
            assert dd.load_variants("2022-06") == {"k2": "k1", "k3": "k1"}
        finally:
            dd.gconfig.JD_DERIVED_DIR = real
