# -*- coding: utf-8 -*-
"""新闻映射优先惰性解析（2026-08-31）：窗口运行不再全量走盘 28 万篇。

- scan_news_metadata：映射缺失/失步（超容差）→ (None, reason) 回退全量；
  轻微偏差按交集继续；顺序按 source_file 排序（确定性）；
- parse_news_selected：只解析指定集合，过短文件跳过计数；
- news_delta 窗口路径在 light 行上过滤+抽样（doc_id 兼容），抽样后惰性解析。
"""
import csv
import json
import os
import sys

import ut

_HERE = os.path.dirname(os.path.abspath(__file__))
_NEWS_DIR = ut.path("news_signal")
_BUILDER_DIR = ut.path("builder")


def _news_parser():
    for m in ("config", "llm"):
        sys.modules.pop(m, None)   # 跨包冲突名按本助手的路径序重新解析
    for d in (_NEWS_DIR, _BUILDER_DIR):
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
    import importlib
    return importlib.import_module("news_parser")


def _mk_corpus(tmp, files):
    """files: {rel_path: content}，建 news_raw 结构。"""
    root = tmp / "news_raw"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return str(root)


def _mk_mapping(tmp, rows):
    path = tmp / "news_mapping.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source_file", "doc_id", "source", "title", "pub_date", "crawled_at"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


BODY = "标题行\n正文" + "内容" * 120 + "\n"   # 折叠后 >200 字（MIN_BODY_CHARS）


def test_scan_news_metadata_sync_and_fallback(tmp_path):
    """轻量扫描（不读正文）与目录结构一致；无缓存/损坏缓存回退重扫。"""
    np = _news_parser()
    root = _mk_corpus(tmp_path, {"srcA/a.txt": BODY, "srcA/b.txt": BODY, "srcB/c.txt": BODY})
    # 同步：映射=盘上 → light 三行、有序、doc_id 去 .txt
    mp = _mk_mapping(tmp_path, [
        {"source_file": "srcA/a.txt", "doc_id": "srcA/a", "source": "A", "title": "t1", "pub_date": "2024-02-01", "crawled_at": ""},
        {"source_file": "srcB/c.txt", "doc_id": "srcB/c", "source": "B", "title": "t2", "pub_date": "2024-02-02", "crawled_at": ""},
        {"source_file": "srcA/b.txt", "doc_id": "srcA/b", "source": "A", "title": "t3", "pub_date": "", "crawled_at": ""},
    ])
    light, why = np.scan_news_metadata(root, mapping_path=mp)
    assert light is not None, why
    assert [r["source_file"] for r in light] == [os.path.join("srcA", "a.txt"),
                                                 os.path.join("srcA", "b.txt"),
                                                 os.path.join("srcB", "c.txt")], "按 source_file 排序"
    assert light[0]["doc_id"] == os.path.join("srcA", "a")
    # 轻微偏差（1 个盘上多余 < 容差 5）→ 交集继续
    (tmp_path / "news_raw" / "srcA" / "new.txt").write_text(BODY, encoding="utf-8")
    light2, why2 = np.scan_news_metadata(root, mapping_path=mp)
    assert light2 is not None and len(light2) == 3
    # 失步（> 容差）→ 回退
    for i in range(10):
        (tmp_path / "news_raw" / "srcA" / f"x{i}.txt").write_text(BODY, encoding="utf-8")
    light3, why3 = np.scan_news_metadata(root, mapping_path=mp)
    assert light3 is None and "失步" in why3
    # 映射缺失 → 回退
    light4, why4 = np.scan_news_metadata(root, mapping_path=str(tmp_path / "none.csv"))
    assert light4 is None


def test_parse_news_selected(tmp_path):
    """按选定 doc_id 惰性解析正文（body + file_md5 完整性）。"""
    np = _news_parser()
    root = _mk_corpus(tmp_path, {"s/a.txt": BODY, "s/b.txt": "太短"})
    recs = np.parse_news_selected([os.path.join("s", "a.txt"), os.path.join("s", "b.txt")], news_dir=root)
    assert len(recs) == 1 and recs[0].doc_id == os.path.join("s", "a")
    assert recs[0].body and recs[0].file_md5


def test_news_delta_window_lazy_wiring(tmp_path, monkeypatch):
    """窗口路径三函数协作：light 池 → 抽样（确定性+记录）→ 惰性解析。"""
    for d in (_NEWS_DIR, _BUILDER_DIR):
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
    for m in ("config", "llm"):
        sys.modules.pop(m, None)   # 跨包冲突名按本助手的路径序重新解析
    import news_delta as nd
    import types
    np = _news_parser()
    monkeypatch.setattr(nd.config, "NEWS_SAMPLE_CAP", 2)
    monkeypatch.setattr(nd.config, "NEWS_DERIVED_DIR", str(tmp_path))
    files = {f"src/m{i}.txt": BODY for i in range(3)}
    root = _mk_corpus(tmp_path, files)
    mp = _mk_mapping(tmp_path, [
        {"source_file": f"src/m{i}.txt", "doc_id": f"src/m{i}", "source": "s",
         "title": f"t{i}", "pub_date": "2024-02-1" + str(i), "crawled_at": ""} for i in range(3)])
    light, why = np.scan_news_metadata(root, mapping_path=mp)
    assert light is not None, why
    pool = [r for r in light if r["pub_date"].startswith("2024-02")]
    objs = [types.SimpleNamespace(**r) for r in pool]
    sampled = nd._apply_sample_cap("2024-02", objs)
    assert len(sampled) == 2
    recs = np.parse_news_selected([o.source_file for o in sampled], root)
    assert len(recs) == 2 and {r.doc_id for r in recs} == {o.doc_id for o in sampled}
    rec_json = json.load(open(tmp_path / "2024-02.sample.json", encoding="utf-8"))
    assert rec_json["n_sampled"] == 2 and set(rec_json["doc_ids"]) == {r.doc_id for r in recs}
    # 同状态重跑：抽样逐条一致（确定性）
    sampled2 = nd._apply_sample_cap("2024-02", objs)
    assert [o.doc_id for o in sampled2] == [o.doc_id for o in sampled]
