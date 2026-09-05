# -*- coding: utf-8 -*-
"""
funtype 名称合并 + IT 判定（规则 + LLM 结合）

对 DB 中所有 funtype 部分（all_funtype_parts.json）：
  1. 已在既有 51job 体系中的名称 → 直接复用其 IT 判断（source=classify）
  2. 明显变体（规则：去括号/去"开发工程师"等后缀后精确命中体系名）→ 合并到体系名并复用其判断（source=rule_merge）
  3. 其余名称 → 交给 deepseek-v4-flash：
       - 若能语义归类到既有 IT 分类（含变体/同义/包含）→ matched_to 该分类，视为 IT（source=llm_merge）
       - 否则直接判定是否 IT 相关（source=llm）

输出 output/funtype_it_map.json：
  part -> {"it_related": bool, "confidence": float, "reason": str, "matched_to": str|null, "source": str}
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_classify_51job")))
import classify_jobs as cj  # 复用 call_api / load_api_key

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "funtype_it_map.json")
PARTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "all_funtype_parts.json")
DD = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "classify", "docs", "51job_classify", "dd_funtype_translation.json"))
JUDGE_OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_classify_51job", "output", "51job_it_jobs_classified.json"))

BATCH = 20
MAX_TOKENS = 8000

SUFFIXES = ["开发工程师", "研发工程师", "工程师", "开发", "设计师", "经理", "主管", "专员", "负责人"]


def load_system():
    dd = json.load(open(DD, encoding="utf-8"))
    system_names = set(v["value"] for v in dd.values())
    d = json.load(open(JUDGE_OUT, encoding="utf-8"))
    name_it = {}
    for code, j in d["judgments_by_node"].items():
        name_it.setdefault(j["name"], []).append((j["it_related"], j["confidence"]))
    return system_names, name_it


def rule_merge(part, system_names):
    """返回命中的体系名；未命中返回 None。只做保守精确匹配，避免误合并。"""
    base = re.sub(r"[（(].*?[）)]", "", part).strip()
    if not base:
        return None
    if base in system_names:
        return base
    # 去后缀后精确命中
    for suf in sorted(SUFFIXES, key=len, reverse=True):
        if base.endswith(suf) and len(base) > len(suf):
            cand = base[: -len(suf)].strip()
            if cand in system_names:
                return cand
            break
    return None


PROMPT = """你是招聘岗位分类专家。以下是现有的**信息技术（IT）相关岗位分类**名称列表：
{it_names}

对于以下每个岗位名称：
- 若它能**语义上归类**到上述某个 IT 分类（包括变体写法、同义表达、包含关系；例如 "Go开发工程师" 归类到 "Golang"，"Android开发工程师" 归类到移动开发类），输出 {{"name":"原样","matched_to":"该分类名称","it_related":true,"confidence":0到1,"reason":"不超过15字"}}
- 若不能匹配到上述任何分类，则判断它本身是否属于信息技术相关岗位，输出 {{"name":"原样","matched_to":null,"it_related":true或false,"confidence":0到1,"reason":"不超过15字"}}
严格只输出一个 JSON 数组，不输出任何其他文字。
岗位名称列表：
{names}"""


def classify_batch(key, model, names, it_names, depth=0):
    result = {}
    if not names:
        return result
    prompt = PROMPT.replace("{it_names}", json.dumps(it_names, ensure_ascii=False)) \
                   .replace("{names}", json.dumps(names, ensure_ascii=False))
    try:
        entries = cj.call_api(key, model, prompt, max_tokens=MAX_TOKENS, disable_thinking=True)
        for e in entries:
            nm = e.get("name", "")
            if nm in names:
                mt = e.get("matched_to") or None
                result[nm] = {
                    "it_related": bool(e.get("it_related")),
                    "confidence": float(e.get("confidence", 0.5)),
                    "reason": e.get("reason", ""),
                    "matched_to": mt,
                    "source": "llm_merge" if mt else "llm",
                }
        missing = [n for n in names if n not in result]
        if missing and depth < 3 and len(missing) > 1:
            mid = len(missing) // 2
            result.update(classify_batch(key, model, missing[:mid], it_names, depth + 1))
            result.update(classify_batch(key, model, missing[mid:], it_names, depth + 1))
        else:
            for n in missing:
                result[n] = {"it_related": False, "confidence": 0.0, "reason": "缺失",
                             "matched_to": None, "source": "llm"}
        return result
    except Exception as e:
        if len(names) > 1 and depth < 4:
            mid = len(names) // 2
            time.sleep(2)
            result.update(classify_batch(key, model, names[:mid], it_names, depth + 1))
            result.update(classify_batch(key, model, names[mid:], it_names, depth + 1))
            return result
        for n in names:
            result[n] = {"it_related": False, "confidence": 0.0, "reason": f"API失败:{str(e)[:30]}",
                         "matched_to": None, "source": "llm"}
        return result


def main():
    parts_data = json.load(open(PARTS, encoding="utf-8"))
    all_parts = list(parts_data["parts"].keys())
    system_names, name_it = load_system()

    it_only = {n for n, l in name_it.items() if all(x[0] for x in l)}
    nonit_only = {n for n, l in name_it.items() if not any(x[0] for x in l)}

    final_map = {}

    # 1. 既有体系直接判定
    for p in all_parts:
        if p in it_only:
            final_map[p] = {"it_related": True, "confidence": max(c for _, c in name_it[p]),
                            "reason": "体系内IT", "matched_to": p, "source": "classify"}
        elif p in nonit_only:
            final_map[p] = {"it_related": False, "confidence": max(c for _, c in name_it[p]),
                            "reason": "体系内非IT", "matched_to": p, "source": "classify"}

    # 2. 规则合并（明显变体）
    rule_merged = {}
    for p in all_parts:
        if p in final_map:
            continue
        m = rule_merge(p, system_names)
        if m:
            if m in it_only:
                final_map[p] = {"it_related": True, "confidence": 0.95, "reason": f"规则合并自{m}",
                                "matched_to": m, "source": "rule_merge"}
            else:
                final_map[p] = {"it_related": False, "confidence": 0.95, "reason": f"规则合并自{m}",
                                "matched_to": m, "source": "rule_merge"}
            rule_merged[p] = m

    # 3. LLM 语义归类 + 判定
    need_llm = [p for p in all_parts if p not in final_map]
    print(f"总部分: {len(all_parts)} | 体系判定: {sum(1 for v in final_map.values() if v['source']=='classify')} "
          f"| 规则合并: {len(rule_merged)} | 需LLM: {len(need_llm)}", flush=True)

    if need_llm:
        api_key = cj.load_api_key("")
        model = os.environ.get("DEEPSEEK_MODEL", cj.DEFAULT_MODEL)
        it_names = sorted(it_only)
        print(f"LLM 处理 {len(need_llm)} 个（batch={BATCH}，IT参照 {len(it_names)} 个）...", flush=True)
        llm_res = {}
        for i in range(0, len(need_llm), BATCH):
            chunk = need_llm[i:i + BATCH]
            llm_res.update(classify_batch(api_key, model, chunk, it_names))
            n_done = len(llm_res)
            print(f"  进度 {n_done}/{len(need_llm)}（{n_done/len(need_llm)*100:.1f}%）", flush=True)
        n_merge = sum(1 for v in llm_res.values() if v["source"] == "llm_merge")
        print(f"  LLM 完成：语义归类 {n_merge}，直接判定 {len(llm_res) - n_merge}", flush=True)
        final_map.update(llm_res)

    # 输出
    result = {"total_parts": len(all_parts), "parts": {p: final_map.get(p, {
        "it_related": False, "confidence": 0, "reason": "未判定", "matched_to": None, "source": "unknown"}) for p in all_parts}}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    it_n = sum(1 for v in result["parts"].values() if v["it_related"])
    src = {}
    for v in result["parts"].values():
        src[v["source"]] = src.get(v["source"], 0) + 1
    print(f"\n完成！IT 相关 funtype: {it_n}/{len(all_parts)} | 来源分布: {src}")
    print(f"输出: {OUT}")


if __name__ == "__main__":
    main()
