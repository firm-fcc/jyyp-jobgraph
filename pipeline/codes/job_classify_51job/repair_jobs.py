# -*- coding: utf-8 -*-
"""
修复 51job 岗位分类：
1. 从 progress.jsonl 读取已有判断（修正了路径分隔符不一致的问题）
2. 重跑所有 "API失败" 的路径（小批量 + 更大 max_tokens + 失败自动拆半）
3. 重建最终输出 JSON（judgments_by_node + it_classification）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify_jobs as cj

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "51job_it_jobs_classified.json")
PROGRESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "progress.jsonl")
INPUT = "classify/docs/51job_classify/dd_funtype_translation.json"

BATCH = 15
MAX_TOKENS = 8000


def load_progress(progress_file):
    judgments = {}
    if os.path.exists(progress_file):
        with open(progress_file, encoding="utf-8") as f:
            for line in f:
                judgments.update(json.loads(line))
    return judgments


def classify_batch(key, model, paths, depth=0):
    """递归分类：整批失败则拆半重试。返回 dict path -> judgment"""
    result = {}
    if not paths:
        return result
    prompt = cj.PROMPT_TMPL.replace("{paths}", json.dumps(paths, ensure_ascii=False))
    try:
        entries = cj.call_api(key, model, prompt, max_tokens=MAX_TOKENS)
        for e in entries:
            p = e.get("path", "")
            if p in paths:
                result[p] = {
                    "it_related": bool(e.get("it_related")),
                    "confidence": float(e.get("confidence", 0.5)),
                    "reason": e.get("reason", ""),
                }
        missing = [p for p in paths if p not in result]
        if missing:
            # 小批补一次
            if len(missing) > 1 and depth < 3:
                print(f"    [split] 缺失 {len(missing)} 条，拆半递归", flush=True)
                mid = len(missing) // 2
                result.update(classify_batch(key, model, missing[:mid], depth + 1))
                result.update(classify_batch(key, model, missing[mid:], depth + 1))
            else:
                for p in missing:
                    result[p] = {"it_related": False, "confidence": 0.0, "reason": "缺失"}
        return result
    except Exception as e:
        if len(paths) > 1 and depth < 4:
            mid = len(paths) // 2
            print(f"    [split] 批次失败({str(e)[:40]})，拆半递归 depth={depth}", flush=True)
            time.sleep(2)
            result.update(classify_batch(key, model, paths[:mid], depth + 1))
            result.update(classify_batch(key, model, paths[mid:], depth + 1))
            return result
        for p in paths:
            result[p] = {"it_related": False, "confidence": 0.0, "reason": f"API失败:{str(e)[:30]}"}
        return result


def main():
    api_key = cj.load_api_key("")
    model = os.environ.get("DEEPSEEK_MODEL", cj.DEFAULT_MODEL)

    path_judgments = load_progress(PROGRESS)
    print(f"已有判断: {len(path_judgments)} 条", flush=True)

    failed = [p for p, j in path_judgments.items() if str(j.get("reason", "")).startswith("API失败")]
    missing = [p for p, j in path_judgments.items() if str(j.get("reason", "")) == "缺失"]
    print(f"API失败: {len(failed)} 条 | 缺失: {len(missing)} 条", flush=True)

    if failed:
        print(f"重跑 {len(failed)} 条失败路径（batch={BATCH}, max_tokens={MAX_TOKENS}）...", flush=True)
        new_j = classify_batch(api_key, model, failed)
        n_ok = sum(1 for j in new_j.values() if not j["reason"].startswith("API失败"))
        print(f"  补跑完成，成功 {n_ok}/{len(failed)}", flush=True)
        path_judgments.update(new_j)
        # 写回 progress.jsonl
        with open(PROGRESS, "a", encoding="utf-8") as f:
            f.write(json.dumps(new_j, ensure_ascii=False) + "\n")

    # 重建输出
    nodes = cj.load_nodes(INPUT)
    path_map = cj.build_path_map(nodes)
    node_judgments = cj.build_node_judgments(nodes, path_map, path_judgments)
    it_tree = cj.build_it_tree(nodes, node_judgments)

    it_count = sum(1 for j in node_judgments.values() if j["it_related"])
    result = {
        "meta": {
            "source": "51job dd_funtype_translation.json",
            "model": model,
            "date": "2026-08-05",
            "total_nodes": len(nodes),
            "total_unique_paths": len(path_judgments),
            "it_related_nodes": it_count,
            "note": "it_related=True 判定为信息技术相关；confidence 为模型置信度",
        },
        "judgments_by_node": node_judgments,
        "it_classification": it_tree,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n重建完成！IT 相关节点: {it_count} / {len(nodes)}")
    print(f"输出: {OUTPUT}")


if __name__ == "__main__":
    main()
