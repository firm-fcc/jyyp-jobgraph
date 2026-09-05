# -*- coding: utf-8 -*-
"""论文 ΔG 运行跟踪日志：人类可读（markdown）+ 结构化（JSONL）（迁自 paper_signal）。

事件：run_start / batch_start / extract / map / apply / batch_end / note / error。
记录每条信号与映射裁决（含拒绝理由），供人类专家检验信号质量与噪声归因。
输出（默认 classify/DeltaG/ 下，CLI --log 可改前缀）：
- paper_signal_log.jsonl  结构化事件流
- paper_signal_log.md     人类可读 markdown 渲染
"""
import json
import os
from datetime import datetime

import config


class RunLogger:
    """追加式运行日志（两个文件：JSONL 结构化 + markdown 可读）。"""

    def __init__(self, jsonl_path=None, md_path=None, enabled=True):
        self.enabled = enabled
        self.jsonl_path = jsonl_path or config.DELTA_LOG
        self.md_path = md_path or self.jsonl_path.rsplit(".", 1)[0] + ".md"

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
        self._json("run_start", source=source, action=action)
        self._md(f"## [{action}] @ {self._now()}（source={source}）")

    def batch_start(self, round_no, paper_ids):
        self._json("batch_start", round_no=round_no, papers=paper_ids)
        self._md(f"### 批 {round_no}：{len(paper_ids)} 篇 {' '.join(paper_ids)}")

    def extract(self, round_no, candidates):
        self._json("extract", round_no=round_no, n=len(candidates),
                   signals=[{"doc": getattr(c.record, "doc_id", None) or getattr(c.record, "arxiv_id", ""),
                             "kind": c.kind, "name_zh": c.name_zh,
                             "confidence": c.confidence, "rationale": c.rationale}
                            for c in candidates])
        if not candidates:
            self._md(f"- 批{round_no} 提取 0 条信号")
            return
        lines = [f"- 批{round_no} 提取 {len(candidates)} 条信号"]
        for c in candidates:
            lines.append(f"  - [{c.kind}] {c.name_zh} | {c.confidence} | {c.rationale}")
        self._md("\n".join(lines))

    def map(self, round_no, decisions):
        self._json("map", round_no=round_no, n=len(decisions),
                   decisions=[{"index": d.index, "final_kind": d.final_kind, "name_zh": d.name_zh,
                               "status": d.status, "map_to": d.map_to, "merge_into": d.merge_into,
                               "reject_reason": d.reject_reason, "reason": d.reason}
                              for d in decisions])
        lines = [f"- 批{round_no} 映射 {len(decisions)} 条裁决"]
        for d in decisions:
            if d.status == "reject":
                lines.append(f"  - ✗ {d.name_zh}（{d.reject_reason}）")
            elif d.map_to:
                lines.append(f"  - → {d.map_to['taxonomy']}:{d.map_to['code']} {d.name_zh}（{d.reason}）")
            elif d.merge_into:
                lines.append(f"  - ⇄ {d.merge_into} {d.name_zh}（{d.reason}）")
            else:
                lines.append(f"  - ✓ 新增 {d.final_kind} {d.name_zh}（{d.reason}）")
        self._md("\n".join(lines))

    def apply(self, round_no, actions, rejects):
        self._json("apply", round_no=round_no, n_actions=len(actions), n_rejects=len(rejects),
                   actions=actions, rejects=rejects)
        if actions:
            self._md(f"- 批{round_no} 应用 {len(actions)} 项：`{'；'.join(actions)}`")
        if rejects:
            detail = "；".join(f"{r['name_zh']}（{r['reason']}）" for r in rejects)
            self._md(f"- 批{round_no} 拒绝 {len(rejects)} 项：{detail}")

    def batch_end(self, round_no, n_tasks, n_skills, n_jobs):
        self._json("batch_end", round_no=round_no, n_tasks=n_tasks, n_skills=n_skills, n_jobs=n_jobs)
        self._md(f"- 批{round_no} 结束：新任务 {n_tasks} / 新技能 {n_skills} / 新岗位 {n_jobs}")

    def note(self, message):
        self._json("note", message=message)
        self._md(f"- {message}")

    def error(self, stage, message):
        # _json 首位形参即 stage（事件名），子阶段只能换名传，避免关键字冲突
        self._json("error", error_stage=stage, message=message)
        self._md(f"- **错误[{stage}]**：{message}")
