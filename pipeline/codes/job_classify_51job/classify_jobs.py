# -*- coding: utf-8 -*-
"""
51job 岗位分类 —— 信息技术相关岗位筛选

从 51job 岗位职能全量分类（dd_funtype_translation.json）中，用大模型（deepseek-v4-flash）
逐条判断每个岗位节点是否属于信息技术（IT）相关岗位，输出带置信度的判断结果，
并据此构建 IT 岗位分类体系（树）。

用法：
    python classify_jobs.py \
        --input  classify/docs/51job_classify/dd_funtype_translation.json \
        --output codes/job_classify_51job/output/51job_it_jobs_classified.json \
        [--batch 40] [--model deepseek-v4-flash] [--resume]

API key 读取优先级：
    1. --api-key 参数
    2. 项目约定文件 codes/api-key.txt（相对本脚本为 ../api-key.txt，格式 "(provider) label: sk-xxx"）
    3. 环境变量 DEEPSEEK_API_KEY / ANTHROPIC_AUTH_TOKEN
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api-key.txt")


def load_api_key(arg_key):
    """按优先级解析 API key：参数 > codes/api-key.txt（项目专用）> 环境变量。"""
    if arg_key:
        return arg_key.strip()
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        m = re.search(r"(sk-[A-Za-z0-9]+)", text)
        if m:
            return m.group(1)
    for env_name in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        v = os.environ.get(env_name, "").strip()
        if v:
            return v
    return ""

# 判断"信息技术相关"的提示词（带路径上下文，处理"测试>其他"这类歧义）
PROMPT_TMPL = """你是招聘岗位分类专家。51job 岗位分类的每个节点以"父类 > 子类"形式给出完整路径。
请判断每个岗位路径是否属于**信息技术（IT）相关岗位**。
信息技术相关岗位包括（不限于）：软件开发（前端/后端/移动/游戏/嵌入式/硬件）、算法与人工智能、数据分析与开发、
测试与质量、运维与技术支持、网络与通信、电子/电气/仪器仪表、半导体/芯片、信息安全、IT产品经理/技术项目管理、
架构师/技术管理、需求/实施工程师等。
注意结合路径上下文判断：如"测试"在"质量管理 > 测试"下不属于 IT，在"软件 > 测试"下属于 IT；
"产品经理"在"互联网产品经理"下属于 IT 范畴，"其他产品经理"需谨慎。

严格只输出一个 JSON 数组，不要输出任何其他文字、不要用 Markdown 代码块。
每项格式：{"path":"原样路径","it_related":true或false,"confidence":0到1的小数,"reason":"不超过15字"}
岗位路径列表：
{paths}"""


# ---------------------------------------------------------------- 数据加载与路径构建
def load_nodes(input_path):
    with open(input_path, encoding="utf-8") as f:
        return json.load(f)  # dict: code -> node


def build_path_map(nodes):
    """为每个节点构建"父类 > 子类"路径（多父取第一个）。返回 code -> list[str]"""
    cache = {}

    def path_of(code):
        if code in cache:
            return cache[code]
        node = nodes[code]
        parents = node.get("parentCodeSet") or []
        if not parents:
            cache[code] = [node["value"]]
        else:
            # 多父取第一个，避免无限递归（用简单深度保护）
            cache[code] = path_of(parents[0]) + [node["value"]]
        return cache[code]

    for code in nodes:
        path_of(code)
    return cache


# ---------------------------------------------------------------- API 调用
def call_api(key, model, prompt, max_tokens=8000, timeout=180, retries=4, disable_thinking=False):
    """调用 deepseek API，返回解析后的 JSON 数组；失败抛异常。"""
    body_obj = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if disable_thinking:
        body_obj["thinking"] = {"type": "disabled"}
    body = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            content = resp["choices"][0]["message"].get("content", "")
            return extract_json_array(content)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read()[:200]!r}"
            if e.code in (429, 500, 502, 503):
                wait = 10 * (attempt + 1) + 5
                print(f"  [retry] {last_err}，等待 {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            last_err = f"{type(e).__name__}: {e}"
            wait = 8 * (attempt + 1)
            print(f"  [retry] {last_err}，等待 {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"API 调用失败，重试 {retries} 次后放弃：{last_err}")


def extract_json_array(text):
    """从模型输出中提取 JSON 数组（容忍前后杂讯）。"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"响应中未找到 JSON 数组: {text[:200]!r}")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- 批量分类
def classify_paths(key, model, paths, batch_size, progress_file, resume):
    """逐批调用 API，返回 dict: path -> {it_related, confidence, reason}"""
    # 载入已有进度
    done = {}
    if resume and os.path.exists(progress_file):
        with open(progress_file, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                done.update(rec)
        print(f"已载入进度：{len(done)}/{len(paths)} 条", flush=True)

    pending = [p for p in paths if p not in done]
    print(f"待处理：{len(pending)} 条", flush=True)

    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        prompt = PROMPT_TMPL.replace("{paths}", json.dumps(batch, ensure_ascii=False))
        batch_result = {}
        attempts = 0
        while attempts < 5:
            attempts += 1
            try:
                entries = call_api(key, model, prompt)
                # 按 path 精确匹配
                for e in entries:
                    p = e.get("path", "")
                    if p in batch:
                        batch_result[p] = {
                            "it_related": bool(e.get("it_related")),
                            "confidence": float(e.get("confidence", 0.5)),
                            "reason": e.get("reason", ""),
                        }
                missing = [p for p in batch if p not in batch_result]
                if missing:
                    # 对缺失项单独小批重试
                    print(f"  批次缺失 {len(missing)} 条，单独重试: {missing[:3]}...", flush=True)
                    sub_prompt = PROMPT_TMPL.replace("{paths}", json.dumps(missing, ensure_ascii=False))
                    sub_entries = call_api(key, model, sub_prompt, max_tokens=3000, timeout=120)
                    for e in sub_entries:
                        p = e.get("path", "")
                        if p in missing:
                            batch_result[p] = {
                                "it_related": bool(e.get("it_related")),
                                "confidence": float(e.get("confidence", 0.5)),
                                "reason": e.get("reason", ""),
                            }
                # 最终仍有缺失则报警（保留缺失清单，不阻塞）
                still_missing = [p for p in batch if p not in batch_result]
                if still_missing:
                    print(f"  [warn] 仍缺失 {len(still_missing)} 条: {still_missing[:5]}", flush=True)
                break
            except Exception as e:
                print(f"  [batch-error] {e}，第 {attempts} 次尝试", flush=True)
                if attempts >= 5:
                    # 放弃该批，记为空判断
                    for p in batch:
                        batch_result[p] = {"it_related": False, "confidence": 0.0,
                                           "reason": f"API失败:{str(e)[:30]}"}

        done.update(batch_result)
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(batch_result, ensure_ascii=False) + "\n")
        n_done = len(done)
        print(f"  进度 {n_done}/{len(paths)}（{n_done/len(paths)*100:.1f}%）", flush=True)

    return done


# ---------------------------------------------------------------- 输出组装
def build_node_judgments(nodes, path_map, path_judgments):
    """把 path 级判断映射到每个节点。"""
    node_judgments = {}
    for code, node in nodes.items():
        path = path_map[code]
        key = ">".join(path)
        j = path_judgments.get(key, {"it_related": False, "confidence": 0.0, "reason": "缺失"})
        node_judgments[code] = {
            "code": code,
            "name": node["value"],
            "name_en": node.get("evalue", ""),
            "path": path,
            "parent": (nodes[node["parentCodeSet"][0]]["value"]
                       if node.get("parentCodeSet") else None),
            "it_related": j["it_related"],
            "confidence": j["confidence"],
            "reason": j["reason"],
        }
    return node_judgments


def build_it_tree(nodes, node_judgments):
    """由 IT 节点构建分类树：父节点取最近的 IT 祖先，否则挂到合成根。"""
    it_codes = {c for c, j in node_judgments.items() if j["it_related"]}
    root = {"code": "__ROOT__", "name": "信息技术岗位", "name_en": "IT Jobs",
            "confidence": None, "children": []}
    tree_nodes = {}
    for c in it_codes:
        j = node_judgments[c]
        tree_nodes[c] = {
            "code": c,
            "name": j["name"],
            "name_en": j["name_en"],
            "confidence": j["confidence"],
            "children": [],
        }
    attached = set()
    for c in it_codes:
        node = nodes[c]
        # 向上找最近 IT 祖先
        anc = node.get("parentCodeSet") or []
        target = None
        while anc:
            p = anc[0]
            if p in it_codes:
                target = p
                break
            anc = nodes[p].get("parentCodeSet") or []
        parent_container = tree_nodes[target] if target else root
        parent_container["children"].append(tree_nodes[c])
        attached.add(c)
    return root


def main():
    ap = argparse.ArgumentParser(description="51job IT 岗位分类")
    ap.add_argument("--input", required=True, help="dd_funtype_translation.json 路径")
    ap.add_argument("--output", required=True, help="输出 JSON 路径")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL))
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--resume", action="store_true", help="从进度文件续跑")
    ap.add_argument("--api-key", default="", help="DeepSeek API key（默认读 codes/api-key.txt）")
    args = ap.parse_args()

    api_key = load_api_key(args.api_key)
    if not api_key:
        print("错误：未找到 API key（--api-key / DEEPSEEK_API_KEY / ANTHROPIC_AUTH_TOKEN / codes/api-key.txt）",
              file=sys.stderr)
        sys.exit(1)

    # 1. 加载数据
    nodes = load_nodes(args.input)
    path_map = build_path_map(nodes)
    unique_paths = sorted({">".join(p) for p in path_map.values()})  # 去重排序
    print(f"节点总数: {len(nodes)}，唯一路径数: {len(unique_paths)}", flush=True)

    # 2. 批量分类（进度文件放输出同目录）
    out_dir = os.path.dirname(args.output)
    progress_file = os.path.join(out_dir, "progress.jsonl")
    path_judgments = classify_paths(
        api_key, args.model, unique_paths, args.batch, progress_file, args.resume
    )

    # 3. 节点级判断 + IT 树
    node_judgments = build_node_judgments(nodes, path_map, path_judgments)
    it_tree = build_it_tree(nodes, node_judgments)

    it_count = sum(1 for j in node_judgments.values() if j["it_related"])
    result = {
        "meta": {
            "source": "51job dd_funtype_translation.json",
            "model": args.model,
            "date": "2026-08-05",
            "total_nodes": len(nodes),
            "total_unique_paths": len(unique_paths),
            "it_related_nodes": it_count,
            "note": "it_related=True 判定为信息技术相关；confidence 为模型置信度",
        },
        "judgments_by_node": node_judgments,
        "it_classification": it_tree,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n完成！共 {len(nodes)} 节点，IT 相关 {it_count} 个。")
    print(f"输出: {args.output}")


if __name__ == "__main__":
    main()
