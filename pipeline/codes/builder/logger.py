# -*- coding: utf-8 -*-
"""Builder 运行跟踪日志：人类可读（markdown）+ 结构化（JSONL）。

目的：让人类专家可检验体系构建全过程——
- 冷启动：采样规模、归纳出的任务清单
- 热更新：每轮投喂、提案（含理由）、监督裁决（含拒绝理由 / 合并建议）、应用动作、重检结果
- 若任务数目膨胀：日志记录每轮任务数变化与每项新增的监督依据，可快速定位是哪一轮、哪个提案、
  监督为何批准导致的

输出（默认 classify/Tasks/ 下，CLI --log 可改前缀）：
- builder_log.jsonl  结构化事件流，每行一个 JSON（可程序化分析 / 统计）
- builder_log.md     人类可读 markdown 渲染，含任务数变化与全部决策理由
"""
import json
import os
from datetime import datetime

import config


def _fmt_proposal(u):
    """把单个更新提案渲染为简短可读文本。"""
    action = u.get("action", "?")
    if action == "add":
        return f"add {(u.get('task') or {}).get('name_zh', '')}"
    if action == "merge":
        return f"merge {'/'.join(u.get('merge_codes') or [])}"
    if action == "modify":
        code = u.get("target_code") or (u.get("task") or {}).get("code", "")
        return f"modify {code}"
    return action


class RunLogger:
    """追加式运行日志（两个文件：JSONL 结构化 + markdown 可读）。"""

    def __init__(self, jsonl_path=None, md_path=None, enabled=True):
        self.enabled = enabled
        self.jsonl_path = jsonl_path or config.BUILDER_LOG
        if md_path:
            self.md_path = md_path
        else:
            # 默认与 jsonl 同前缀（builder_log.jsonl -> builder_log.md）
            self.md_path = self.jsonl_path.rsplit(".", 1)[0] + ".md"

    # ---------- 内部写入 ----------
    def _json(self, stage, **data):
        if not self.enabled:
            return
        os.makedirs(os.path.dirname(self.jsonl_path), exist_ok=True)
        entry = {"ts": self._now(), "stage": stage}
        entry.update(data)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _md(self, text):
        if not self.enabled:
            return
        os.makedirs(os.path.dirname(self.md_path), exist_ok=True)
        with open(self.md_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    # ---------- 事件 ----------
    def run_start(self, source, action):
        """记录一次构建动作开始（冷启动 / 热更新 / 全流程）。"""
        self._json("run_start", source=source, action=action)
        self._md(f"## [{action}] @ {self._now()}（source={source}）")

    def cold_start(self, n_docs, tasks):
        """冷启动结果：采样条数 + 归纳任务清单。"""
        t = [{"code": x["code"], "name_zh": x["name_zh"]} for x in tasks]
        self._json("cold_start", n_docs=n_docs, n_tasks=len(t), tasks=t)
        lines = [f"### 冷启动：采样 {n_docs} 条 → 归纳 {len(t)} 个任务"]
        lines += [f"- `{x['code']}` {x['name_zh']}" for x in t]
        self._md("\n".join(lines))

    def hot_round_start(self, round_no, batch_size, chunk=None):
        self._json("hot_round_start", round_no=round_no, chunk=chunk, batch_size=batch_size)
        head = f"### 热更新 round{round_no}" + (f" 子块{chunk}" if chunk else "")
        self._md(f"{head}：投喂 {batch_size} 条新数据")

    def propose(self, round_no, attempt, covered, updates):
        """提案 Agent 输出：是否覆盖 + 全部更新提案（含理由）。"""
        self._json("propose", round_no=round_no, attempt=attempt,
                   covered=covered, n_updates=len(updates), updates=updates)
        if covered and not updates:
            self._md(f"- round{round_no}/尝试{attempt} 提案：本批已被当前体系覆盖，无需更新")
            return
        lines = [f"- round{round_no}/尝试{attempt} 提案：covered={covered}，{len(updates)} 项更新"]
        for u in updates:
            lines.append(f"  - {_fmt_proposal(u)} | {u.get('reason', '')}")
        self._md("\n".join(lines))

    def supervise(self, round_no, attempt, approved, rejected):
        """监督 Agent 裁决：批准 / 拒绝（含拒绝理由与合并建议）。"""
        self._json("supervise", round_no=round_no, attempt=attempt,
                   n_approved=len(approved), n_rejected=len(rejected),
                   approved=[_fmt_proposal(u) for u in approved], rejected=rejected)
        lines = [f"- round{round_no}/尝试{attempt} 监督：批准 {len(approved)} / 拒绝 {len(rejected)}"]
        for u in approved:
            lines.append(f"  - ✓ {_fmt_proposal(u)}")
        for r in rejected:
            mt = r.get("map_to")
            suffix = f"（建议并入 {mt}）" if mt else ""
            lines.append(f"  - ✗ {r.get('reason', '')}{suffix}")
        self._md("\n".join(lines))

    def apply(self, round_no, attempt, apply_log):
        """应用动作结果（增 / 并 / 改）。"""
        self._json("apply", round_no=round_no, attempt=attempt, n=len(apply_log), actions=apply_log)
        self._md(f"- round{round_no}/尝试{attempt} 应用 {len(apply_log)} 项更新：`{'；'.join(apply_log)}`")

    def recheck(self, round_no, attempt, covered):
        """重检本批覆盖情况。"""
        self._json("recheck", round_no=round_no, attempt=attempt, covered=covered)
        self._md(f"- round{round_no}/尝试{attempt} 重检：{'本批已覆盖' if covered else '仍有余量，继续精化'}")

    def hot_round_end(self, round_no, n_tasks, chunk=None):
        """本轮/子块结束时的任务总数（用于追踪膨胀）。"""
        self._json("hot_round_end", round_no=round_no, chunk=chunk, n_tasks=n_tasks)
        tag = f"round{round_no}" + (f"/子块{chunk}" if chunk else "")
        self._md(f"- {tag} 本轮结束，任务总数：**{n_tasks}**")

    def note(self, message):
        """通用提示（数据耗尽、达到重检上限等）。"""
        self._json("note", message=message)
        self._md(f"- {message}")

    def error(self, stage, message):
        # _json 首位形参即 stage（事件名），子阶段只能换名传，避免关键字冲突
        self._json("error", error_stage=stage, message=message)
        self._md(f"- **错误[{stage}]**：{message}")
