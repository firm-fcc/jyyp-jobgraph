# -*- coding: utf-8 -*-
"""转正岗位类别归纳（旁路环节，转正收口后运行；不改 propose→确证→转正主链）。

背景：promotion._write_jobs 转正写入时 category 为空——2026-08-31 前没有归类
环节，GJ-001..005 空类别由前端反馈暴露后人工补齐（v2.6）。自 2026-08-31 起
每次转正收口后运行本模块：对空类别岗位做 LLM 归纳（9 类体系 + 同类现有岗位
清单 → 建议 category/confidence/reason），**人工确认后**写回基准
（先备份 → 填 category → bump version → 记 promotion_log）。

用法（在模块目录下运行）：
  cd codes/builder
  python job_categorize.py                 # 扫描空类别岗位 → LLM 建议 → 人工确认写回
  python job_categorize.py --suggest-only  # 只打印建议不写入
  python job_categorize.py --jobs PATH     # 注入基准路径（测试用）

交互：回车=接受建议；输入 9 类 code（如 AID）=改判；s=跳过本轮该岗位。
非 tty 环境自动降级为 suggest-only（绝不静默写基准）。
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config                                        # noqa: E402  builder config
import llm                                           # noqa: E402
from promotion import _append_log, _bump_version     # noqa: E402  与转正同款收口件

PROMPT_TEMPLATE = """你是信息技术岗位体系的分类专家。给定一级类别体系（{n_cat} 类，含职责描述与各类现有岗位清单）
和一个新转正岗位，判断该岗位最应归属的一级类别。判断依据岗位定义的核心工作内容，而非名称字面。

一级类别体系：
{categories}

新转正岗位：
code: {code}
名称: {name_zh}
英文名: {name_en}
定义: {definition}

输出 JSON（不要输出其他内容）：
{{"category": "上述类别 code 之一", "confidence": 0到1的小数, "reason": "一句话理由", "runner_up": "次选类别 code"}}
"""


def find_uncategorized(jobs_path=None):
    """基准中 category 为空或不在现有类别 code 中的岗位 → [(code, job_dict)]。"""
    jobs_path = jobs_path or config.JOB_TAXONOMY
    data = json.load(open(jobs_path, encoding="utf-8"))
    valid = {c["code"] for c in data.get("categories", [])}
    return [(code, d) for code, d in data.get("detail", {}).items()
            if (d.get("category") or "") not in valid]


def _category_lines(data):
    """prompt 的类别块：每类一行描述 + 现有岗位名清单（纯组织维度，辅助相似度判断）。"""
    by_cat = {}
    for code, d in sorted(data.get("detail", {}).items()):
        by_cat.setdefault(d.get("category") or "", []).append(d.get("name_zh", ""))
    lines = []
    for c in data.get("categories", []):
        jobs = "、".join(x for x in by_cat.get(c["code"], []) if x)
        lines.append(f"{c['code']} {c['name_zh']}：{c.get('description', '')}\n  现有岗位：{jobs}")
    return "\n".join(lines), len(data.get("categories", []))


def build_prompt(code, job, data):
    cats, n_cat = _category_lines(data)
    return PROMPT_TEMPLATE.format(
        n_cat=n_cat, categories=cats, code=code,
        name_zh=job.get("name_zh", ""), name_en=job.get("name_en", ""),
        definition=job.get("definition", "") or "（无定义）")


def suggest_category(code, job, data, api_key=None):
    """LLM 归纳一个岗位 → {category, confidence, reason, runner_up}。

    返回值保证 category ∈ 基准类别 code；LLM 输出不合法时置 error 字段（不抛出，
    由调用方决定跳过或人工直接改判）。"""
    valid = {c["code"] for c in data.get("categories", [])}
    try:
        raw = llm.call_llm(build_prompt(code, job, data), api_key=api_key)
    except Exception as e:                            # 网络/配额等：可重跑，不算失败判定
        return {"category": "", "confidence": 0.0, "reason": f"LLM 调用失败：{e}",
                "runner_up": "", "error": str(e)}
    sug = raw if isinstance(raw, dict) else {}
    if sug.get("category") not in valid:
        return {"category": "", "confidence": 0.0,
                "reason": f"LLM 返回非法类别：{json.dumps(raw, ensure_ascii=False)[:120]}",
                "runner_up": "", "error": "invalid_category"}
    return {"category": sug["category"],
            "confidence": float(sug.get("confidence") or 0.0),
            "reason": sug.get("reason", ""), "runner_up": sug.get("runner_up", "")}


def _confirm(code, name, sug, valid_codes):
    """交互确认 → 类别 code（回车=接受建议 / 9类code=改判 / s=跳过）。"""
    while True:
        ans = input(f"  [{code} {name}] 接受 {sug['category']}（回车）/ 输入改判 code / s 跳过：").strip()
        if ans == "":
            return sug["category"] or None
        if ans.lower() == "s":
            return None
        if ans in valid_codes:
            return ans
        print(f"  非法 code（{'、'.join(valid_codes)}），重输")


def categorize(jobs_path=None, suggest_only=False, assume_yes=False,
               backup_root=None, api_key=None, log_path=None, _suggest=None):
    """主流程：扫描空类别岗位 → LLM 逐个归纳 → 确认 → 一次性写回基准。

    assume_yes：非交互自动接受建议（测试/脚本用；建议为空则跳过）。
    log_path / _suggest：测试注入（临时日志路径 / 替代 LLM 的建议函数），零 LLM。
    """
    jobs_path = jobs_path or config.JOB_TAXONOMY
    data = json.load(open(jobs_path, encoding="utf-8"))
    valid_codes = tuple(c["code"] for c in data.get("categories", []))
    todo = find_uncategorized(jobs_path)
    if not todo:
        print("[categorize] 无空类别岗位，跳过。")
        return {"n_todo": 0, "confirmed": {}}
    print(f"[categorize] 待归类岗位 {len(todo)} 个：")
    confirmed, suggestions = {}, {}

    def _default(code, job, data):
        return suggest_category(code, job, data, api_key=api_key)

    suggest = _suggest or _default
    for code, job in todo:
        sug = suggest(code, job, data)
        suggestions[code] = sug
        tag = f"conf={sug.get('confidence', 0):.2f}" if not sug.get("error") else "FAILED"
        print(f"  [{code} {job.get('name_zh', '')}] 建议 {sug.get('category') or '?'}（{tag}）"
              f"{' 次选 ' + sug['runner_up'] if sug.get('runner_up') else ''}")
        print(f"    理由：{sug.get('reason', '')}")
        if suggest_only:
            continue
        if assume_yes:
            ans = sug.get("category") or None
        else:
            ans = _confirm(code, job.get("name_zh", ""), sug, valid_codes)
        if ans:
            confirmed[code] = ans
    if suggest_only or not confirmed:
        return {"n_todo": len(todo), "confirmed": confirmed, "suggest_only": suggest_only,
                "suggestions": suggestions}
    # 一次性写回：备份 → 填 category → bump version → 记日志
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(backup_root or config.OVERLAY_BACKUP_DIR, f"categorize-{ts}")
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(jobs_path, os.path.join(bdir, os.path.basename(jobs_path)))
    for code, cat in confirmed.items():
        data["detail"][code]["category"] = cat
    _bump_version(data)
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    lines = [f"## {datetime.now().isoformat(timespec='seconds')} 转正后类别归纳",
             f"- 备份：{bdir}",
             f"- 归类 {len(confirmed)} 条："]
    for code, cat in confirmed.items():
        job = data["detail"][code]
        sug = suggestions.get(code, {})
        src = "LLM 建议接受" if sug.get("category") == cat and not sug.get("error") else "人工改判"
        lines.append(f"  - {code} {job.get('name_zh', '')} → {cat}（{src}，"
                     f"confidence={sug.get('confidence', 0)}）")
    log = _append_log("\n".join(lines), log_path=log_path)
    print(f"[categorize] 已写回 {len(confirmed)} 条 → {jobs_path}；备份 {bdir}；日志 {log}")
    return {"n_todo": len(todo), "confirmed": confirmed, "backup_dir": bdir, "log": log}


def main():
    ap = argparse.ArgumentParser(description="转正岗位类别归纳（旁路，人工确认后写回）")
    ap.add_argument("--jobs", default="", help="基准 jobs_v2.json 路径（缺省 config.JOB_TAXONOMY）")
    ap.add_argument("--suggest-only", action="store_true", help="只打印 LLM 建议，不写入")
    args = ap.parse_args()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive and not args.suggest_only:
        print("[categorize] 非 tty 环境，自动降级为 suggest-only（不写基准）。")
    categorize(jobs_path=args.jobs or None, suggest_only=args.suggest_only or not interactive)


if __name__ == "__main__":
    main()
