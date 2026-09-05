# -*- coding: utf-8 -*-
"""图谱快照机制自测：叠层合并 / 日期过滤 / 强度手算 / 空delta / job_links / 幂等 / 校验。

运行：cd codes/graph && python fixtures/test_snapshot.py
（使用 fixtures/ 下的小样本两源 delta，输出到临时目录，不触碰正式产物。）
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date

import ut

ut.setup("graph", "builder")
ut.isolate()

from snapshot_builder import merge_delta, _build_job_links, build_snapshot, parse_window  # noqa: E402
from graph_snapshot import GraphSnapshot            # noqa: E402
import graph_config                                 # noqa: E402  基图文件跟随 taxonomy_base.json 单一开关

FIXTURES = ut.fixture("papers_delta.json"), ut.fixture("news_delta.json")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def _approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


def test_merge_and_filter():
    """叠层合并：证据日期≤窗末保留、无日期保守保留、未来证据排除。"""
    print("== 叠层合并 + 日期过滤 + 强度手算 ==")
    _, start, end, _ = parse_window("2026-05")
    papers, news = _load(FIXTURES[0]), _load(FIXTURES[1])
    merged, stats = merge_delta(papers, news, {}, end)

    _assert(len(merged["new_jobs"]) == 1, f"new_jobs 合并后 1 条（实际 {len(merged['new_jobs'])}）")
    _assert(len(merged["new_tasks"]) == 1, f"new_tasks 合并后 1 条（PT-002 全在窗口外剔除，实际 {len(merged['new_tasks'])}）")
    _assert(len(merged["new_skills"]) == 1, f"new_skills 合并后 1 条（实际 {len(merged['new_skills'])}）")
    _assert(len(merged["strengthenings"]) == 1, f"strengthenings 合并后 1 条（实际 {len(merged['strengthenings'])}）")

    job = merged["new_jobs"][0]
    _assert(job["id"] == "PJ-001", f"合并后 id 取 papers 侧 PJ-001（实际 {job['id']}）")
    _assert(set(job["sources"]) == {"papers", "news"}, f"sources 并集 {job['sources']}")
    _assert(len(job["evidence"]) == 2, f"PJ-001 证据 2 条（paperA+newsX，窗口外 paperB 滤掉；实际 {len(job['evidence'])}）")

    # 强度手算：paperA(tier=S,conf=high) + newsX(conf=high)
    days_p = (end - date(2026, 5, 10)).days          # 21
    days_n = (end - date(2026, 4, 20)).days          # 41
    c_p = 1.0 * 1.0 * (0.5 ** (days_p / 730.0))
    c_n = 0.4 * 1.0 * (0.5 ** (days_n / 180.0))
    expect = 1 - (1 - c_p) * (1 - c_n)
    _assert(_approx(job["strength"], round(expect, 4), 1e-4),
            f"PJ-001 strength={job['strength']} ≈ 手算 {round(expect, 4)}")

    _assert(stats["n_evidence_filtered"] == 4, f"滤掉 4 条窗口外证据（paperB + paperC×2 + newsY；实际 {stats['n_evidence_filtered']}）")
    _assert(stats["n_dropped_no_evidence"] == 3, f"整条被滤空的 3 条（PJ-002×2 源 + PT-002；实际 {stats['n_dropped_no_evidence']}）")
    _assert(stats["n_merged_norm"] == 4, f"norm 合并 4 处（jobs/tasks/skills/strengthenings；实际 {stats['n_merged_norm']}）")
    _assert(stats["n_dateless_evidence"] == 0, f"无日期证据 0（实际 {stats['n_dateless_evidence']}）")


def test_job_links():
    """岗位关联链接：全部 dst 可解析无悬空。"""
    print("== job_links 生成 + 悬空过滤 ==")
    _, start, end, _ = parse_window("2026-05")
    papers, news = _load(FIXTURES[0]), _load(FIXTURES[1])
    merged, _ = merge_delta(papers, news, {}, end)
    # 基图节点跟随 classify/taxonomy_base.json 开关（fixture 引用的技能码须存在于当前基准技能体系）
    base_nodes = {nk: json.load(open(p, encoding="utf-8"))
                  for nk, p in graph_config.BASE_NODE_FILES.items()}
    links = _build_job_links(merged, base_nodes)
    # PJ-001 related_tasks 并集去重后 2 条（T-01 tasks + PT-001 new_tasks），related_skills 1 条（T-AI-01）
    _assert(len(links) == 3, f"job_links 3 条（tasks T-01 + new_tasks PT-001 + skills T-AI-01；实际 {len(links)}）")
    taxs = sorted(l["taxonomy"] for l in links)
    _assert(taxs == ["new_tasks", "skills", "tasks"], f"taxonomy 集合 {taxs}")
    _assert(all(l["dst"] in {"T-01", "PT-001", "T-AI-01"} for l in links), "dst 均能解析（无悬空）")


def test_empty_delta():
    """三源均缺失/为空 → 合法空 ΔG（meta 记 delta_missing）。"""
    print("== 空 delta 合法构建 ==")
    _, start, end, _ = parse_window("2026-05")
    merged, stats = merge_delta({}, {}, {}, end)
    _assert(all(not merged[arr] for arr in merged), "三源均空 → 空 ΔG")


def _strip_created(obj):
    """递归去掉 created 时间戳（构建时刻，非内容）。"""
    if isinstance(obj, dict):
        return {k: _strip_created(v) for k, v in obj.items() if k != "created"}
    if isinstance(obj, list):
        return [_strip_created(v) for v in obj]
    return obj


def _read_content(root):
    """读取一个时间截面全部 JSON 内容（去掉 created）。"""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(dirpath, fn)
            with open(p, encoding="utf-8") as f:
                out[os.path.relpath(p, root)] = _strip_created(json.load(f))
    return out


def test_build_and_idempotent(tmp):
    """构建幂等（force 重建内容一致，忽略时间戳）与已存在拒绝覆盖。"""
    print("== build_snapshot 写文件 + 幂等 ==")
    out_root = os.path.join(tmp, "graph")
    delta_files = {"papers": FIXTURES[0], "news": FIXTURES[1]}
    stats = build_snapshot("2026-05", out_root=out_root, delta_files=delta_files)
    _assert(stats["n_new_jobs"] == 1 and stats["n_new_tasks"] == 1 and stats["n_new_skills"] == 1,
            f"快照叠层统计正确（新岗位{stats['n_new_jobs']}/新任务{stats['n_new_tasks']}/新技能{stats['n_new_skills']}）")
    _assert(stats["n_job_links"] == 3, f"job_links 3 条（实际 {stats['n_job_links']}）")
    # 覆盖拒绝
    try:
        build_snapshot("2026-05", out_root=out_root, delta_files=delta_files)
        _assert(False, "应拒绝覆盖")
    except FileExistsError:
        _assert(True, "已存在窗口拒绝覆盖（FileExistsError）")
    # force 重建 → 内容一致（幂等；created 时间戳除外）
    first = _read_content(os.path.join(out_root, "2026-05"))
    build_snapshot("2026-05", out_root=out_root, delta_files=delta_files, force=True)
    second = _read_content(os.path.join(out_root, "2026-05"))
    _assert(first == second, "force 重建内容一致（忽略 created）")
    # validate 通过 + 边端点可解析
    snap = GraphSnapshot.load("2026-05", out_root)
    errs = snap.validate()
    _assert(not errs, f"validate 通过（错误 {errs}）")
    _assert(len(snap.job_links()) == 3, "加载 API job_links 3 条")


def test_keep_base_edges(tmp):
    """force 重建保留已非空基图边与技能点；--reset-base-edges 才清空。"""
    print("== force 重建保留基图边（keep_base_edges）==")
    out_root = os.path.join(tmp, "graph_keep")
    delta_files = {"papers": FIXTURES[0], "news": FIXTURES[1]}
    build_snapshot("2026-05", out_root=out_root, delta_files=delta_files)
    # 模拟 base_builder 写入的基图边与技能点
    base_dir = os.path.join(out_root, "2026-05", "base")
    edge = {"src": "0107", "dst": "T-01", "src_name": "软件工程师", "dst_name": "x",
            "relation": "job_task", "weight": 0.5}
    with open(os.path.join(base_dir, "job_task.json"), "w", encoding="utf-8") as f:
        json.dump({"system_name": "t", "schema_version": "0.1", "window": "2026-05",
                   "relation": "job_task", "total": 1, "edges": [edge]}, f, ensure_ascii=False)
    with open(os.path.join(base_dir, "skillpoints.json"), "w", encoding="utf-8") as f:
        json.dump({"system_name": "t", "schema_version": "0.1", "window": "2026-05",
                   "total": 1, "skillpoints": {"Python": {"weight": 1.0}}}, f, ensure_ascii=False)
    # force 重建（默认 keep）→ 边与技能点保留
    stats = build_snapshot("2026-05", out_root=out_root, delta_files=delta_files, force=True)
    kept = json.load(open(os.path.join(base_dir, "job_task.json"), encoding="utf-8"))
    _assert(kept.get("total") == 1 and kept["edges"][0]["src"] == "0107",
            "force 重建保留已非空基图边")
    sp = json.load(open(os.path.join(base_dir, "skillpoints.json"), encoding="utf-8"))
    _assert(sp.get("total") == 1, "force 重建保留已非空技能点")
    _assert(stats["n_base_edges"].get("job_task") == 1, "meta.stats 记录保留边计数")
    # reset_base_edges=True → 重置为空
    build_snapshot("2026-05", out_root=out_root, delta_files=delta_files, force=True,
                   keep_base_edges=False)
    reset = json.load(open(os.path.join(base_dir, "job_task.json"), encoding="utf-8"))
    _assert(reset.get("total") == 0, "reset_base_edges 时重置为空 schema")


def main():
    tmp = tempfile.mkdtemp(prefix="graph_fixture_")
    try:
        test_merge_and_filter()
        test_job_links()
        test_empty_delta()
        test_build_and_idempotent(tmp)
        test_keep_base_edges(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
