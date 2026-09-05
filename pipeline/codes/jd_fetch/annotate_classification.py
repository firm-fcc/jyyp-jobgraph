# -*- coding: utf-8 -*-
"""
把 funtype→岗位分类 的映射标注进 docs/job_classification.json，实现可追溯。

标注内容：
  1. 每个 IT 分类节点增加 "funtypes"：映射到该节点的 DB funtype 名称列表（含变体）
  2. 顶层新增 "mapping_log"：规则合并 / LLM 语义归类的明细
  3. meta 增加 funtype_coverage 统计
输入：docs/job_classification.json、codes/jd_fetch/output/funtype_it_map.json
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CLASS_FILE = os.path.join(PROJECT_ROOT, "docs", "job_classification.json")
MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "funtype_it_map.json")


def walk(nodes):
    """遍历层级树，返回 name -> node 引用。"""
    name_to_node = {}
    stack = list(nodes)
    while stack:
        n = stack.pop()
        name_to_node[n["name"]] = n
        stack.extend(n.get("children", []))
    return name_to_node


def main():
    cls = json.load(open(CLASS_FILE, encoding="utf-8"))
    it_map = json.load(open(MAP_FILE, encoding="utf-8"))["parts"]

    name_to_node = walk(cls["hierarchy"])
    # 每个分类节点名 -> 映射到它的 funtype parts
    node_funtypes = defaultdict(list)
    rule_merges, llm_merges = [], []
    it_direct, nonit_direct = [], []  # LLM 直接判定、无 matched_to

    for part, j in it_map.items():
        mt = j.get("matched_to")
        src = j.get("source")
        if mt and mt in name_to_node:
            node_funtypes[mt].append(part)
            if src == "rule_merge":
                rule_merges.append({"part": part, "matched_to": mt})
            elif src == "llm_merge":
                llm_merges.append({"part": part, "matched_to": mt})
        else:
            if j.get("it_related"):
                it_direct.append({"part": part, "confidence": j.get("confidence"), "reason": j.get("reason")})
            else:
                nonit_direct.append(part)

    # 标注到节点
    for name, parts in node_funtypes.items():
        if name in name_to_node:
            name_to_node[name]["funtypes"] = sorted(parts)

    # 统计
    source_dist = defaultdict(int)
    for j in it_map.values():
        source_dist[j.get("source", "unknown")] += 1
    it_parts = sum(1 for j in it_map.values() if j["it_related"])

    cls["mapping_log"] = {
        "description": "DB funtype → 岗位分类 映射记录（可追溯）",
        "rule_merges": rule_merges,
        "llm_merges": llm_merges,
        "llm_direct_it": it_direct,
        "llm_direct_nonit_count": len(nonit_direct),
        "full_map_file": "codes/jd_fetch/output/funtype_it_map.json",
    }
    cls["meta"]["funtype_coverage"] = {
        "total_funtype_parts": len(it_map),
        "it_parts": it_parts,
        "non_it_parts": len(it_map) - it_parts,
        "source_distribution": dict(source_dist),
    }

    with open(CLASS_FILE, "w", encoding="utf-8") as f:
        json.dump(cls, f, ensure_ascii=False, indent=2)
    print(f"标注完成：IT 节点数 {len(node_funtypes)} 个挂载 funtype")
    print(f"规则合并 {len(rule_merges)} | LLM 语义归类 {len(llm_merges)} | LLM 直接判 IT {len(it_direct)}")
    print(f"funtype 覆盖：IT {it_parts} / 非IT {len(it_map) - it_parts} / 总数 {len(it_map)}")
    print(f"输出: {CLASS_FILE}")


if __name__ == "__main__":
    main()
