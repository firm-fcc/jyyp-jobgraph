# -*- coding: utf-8 -*-
"""
对 DB 中 51job 体系外的 funtype 名称用 deepseek-v4-flash 做 IT 判定，产出完整映射。

输出 output/funtype_it_map.json：
{
  "part": {"it_related": bool, "confidence": float, "reason": str, "source": "classify|llm"},
  ...
}
source: classify = 来自已有 51job 岗位体系判断；llm = 本次 LLM 判定
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_classify_51job")))
import classify_jobs as cj  # 复用 call_api / PROMPT_TMPL / load_api_key

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "funtype_it_map.json")
PARTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "all_funtype_parts.json")
JUDGE_OUT = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_classify_51job", "output")), "51job_it_jobs_classified.json")

BATCH = 25
MAX_TOKENS = 8000

PROMPT = """你是招聘岗位分类专家。判断以下每个岗位名称是否属于**信息技术（IT）相关岗位**。
IT 相关岗位包括（不限于）：软件开发（前端/后端/移动/游戏/嵌入式/硬件）、算法与人工智能、数据分析与开发、
测试与质量、运维与技术支持、网络与通信、电子/电气/仪器仪表、半导体/芯片、信息安全、IT产品经理/技术项目管理、
架构师/技术管理等。注意：名称不明确时（如"测试""产品经理""项目总监"）根据常见语境判断并适当降低置信度。
严格只输出一个 JSON 数组，不输出任何其他文字。
每项格式：{"name":"原样名称","it_related":true或false,"confidence":0到1的小数,"reason":"不超过15字"}
岗位名称列表：
{names}"""


def load_name_judgments():
    d = json.load(open(JUDGE_OUT, encoding="utf-8"))
    from collections import defaultdict
    name_map = defaultdict(list)
    for code, j in d["judgments_by_node"].items():
        name_map[j["name"]].append((j["it_related"], j["confidence"]))
    it_only = {n for n, l in name_map.items() if all(x[0] for x in l)}
    nonit_only = {n for n, l in name_map.items() if not any(x[0] for x in l)}
    # 名称 → 汇总（歧义时取 IT 置信度最高的）
    name_judge = {}
    for n, l in name_map.items():
        its = [x for x in l if x[0]]
        if its:
            name_judge[n] = {"it_related": True, "confidence": max(c for _, c in its), "source": "classify"}
        else:
            name_judge[n] = {"it_related": False, "confidence": max(c for _, c in l), "source": "classify"}
    return name_judge, it_only, nonit_only


def classify_batch(key, model, names, depth=0):
    result = {}
    if not names:
        return result
    prompt = PROMPT.replace("{names}", json.dumps(names, ensure_ascii=False))
    try:
        entries = cj.call_api(key, model, prompt, max_tokens=MAX_TOKENS)
        for e in entries:
            nm = e.get("name", "")
            if nm in names:
                result[nm] = {"it_related": bool(e.get("it_related")),
                              "confidence": float(e.get("confidence", 0.5)),
                              "reason": e.get("reason", ""), "source": "llm"}
        missing = [n for n in names if n not in result]
        if missing and depth < 3 and len(missing) > 1:
            mid = len(missing) // 2
            result.update(classify_batch(key, model, missing[:mid], depth + 1))
            result.update(classify_batch(key, model, missing[mid:], depth + 1))
        else:
            for n in missing:
                result[n] = {"it_related": False, "confidence": 0.0, "reason": "缺失", "source": "llm"}
        return result
    except Exception as e:
        if len(names) > 1 and depth < 4:
            mid = len(names) // 2
            time.sleep(2)
            result.update(classify_batch(key, model, names[:mid], depth + 1))
            result.update(classify_batch(key, model, names[mid:], depth + 1))
            return result
        for n in names:
            result[n] = {"it_related": False, "confidence": 0.0, "reason": f"API失败:{str(e)[:30]}", "source": "llm"}
        return result


def main():
    parts_data = json.load(open(PARTS, encoding="utf-8"))
    all_parts = list(parts_data["parts"].keys())
    name_judge, it_only, nonit_only = load_name_judgments()

    # 已有判断直接复用（it_only 或 nonit_only 的名称）
    final_map = {n: dict(v) for n, v in name_judge.items() if n in all_parts}
    # 需要 LLM：既不在"明确IT"也不在"明确非IT"（含歧义名 + 体系外名称）
    need_llm = [p for p in all_parts if p not in it_only and p not in nonit_only]
    print(f"总 funtype 部分: {len(all_parts)} | 体系内已知: {len(final_map)} | 需 LLM 判定: {len(need_llm)}", flush=True)

    if need_llm:
        api_key = cj.load_api_key("")
        model = os.environ.get("DEEPSEEK_MODEL", cj.DEFAULT_MODEL)
        print(f"开始 LLM 判定 {len(need_llm)} 个（batch={BATCH}）...", flush=True)
        llm_result = classify_batch(api_key, model, need_llm)
        n_ok = sum(1 for j in llm_result.values() if not j["reason"].startswith("API失败") and j["reason"] != "缺失")
        print(f"  LLM 判定完成，有效 {n_ok}/{len(need_llm)}", flush=True)
        final_map.update(llm_result)

    result = {"total_parts": len(all_parts), "parts": {p: final_map.get(p, {"it_related": False, "confidence": 0, "reason": "未判定"}) for p in all_parts}}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    it_n = sum(1 for v in result["parts"].values() if v["it_related"])
    print(f"完成！IT 相关 funtype: {it_n} / {len(all_parts)}")
    print(f"输出: {OUT}")


if __name__ == "__main__":
    main()
