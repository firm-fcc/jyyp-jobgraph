# -*- coding: utf-8 -*-
"""分类体系加载：技能体系 / 任务体系。

输出统一的标签列表：[{"code": "...", "name_zh": "...", "name_en": "..."}]
供 prompt 与提取逻辑使用。
"""
import json
import os

import config


class Taxonomy:
    def __init__(self, labels, mode, name):
        self.mode = mode          # "skill" | "task"
        self.name = name
        self.labels = labels      # [{"code", "name_zh", "name_en"}]
        self.code_to_name = {l["code"]: l["name_zh"] for l in labels}
        self.name_to_code = {l["name_zh"]: l["code"] for l in labels}

    def label_text(self):
        """生成提示词中的标签列表文本。"""
        return "\n".join(f"{l['code']}:{l['name_zh']}" for l in self.labels)

    def __len__(self):
        return len(self.labels)


def load_skills():
    """从技能体系基准文件加载（当前 skills0821.json，扁平为叶子技能标签）。"""
    path = config.SKILL_TAXONOMY
    data = json.load(open(path, encoding="utf-8"))
    labels = []
    # 基准文件结构: detail.code -> {code, name_zh, name_en, definition, skill_type}
    for code, d in data.get("detail", {}).items():
        labels.append({"code": d["code"], "name_zh": d["name_zh"], "name_en": d.get("name_en", "")})
    return Taxonomy(labels, "skill", data.get("system_name", "技能体系"))


def load_tasks():
    """加载任务体系：优先 Builder 构建的 tasks.json，回退种子 tasks_seed.json。"""
    path = config.TASK_TAXONOMY
    if not os.path.exists(path):
        path = config.TASK_TAXONOMY_SEED
    data = json.load(open(path, encoding="utf-8"))
    labels = [{"code": t["code"], "name_zh": t["name_zh"], "name_en": t.get("name_en", "")}
              for t in data.get("tasks", [])]
    return Taxonomy(labels, "task", data.get("system_name", "任务体系"))


def load_jobs():
    """加载岗位体系：jobs_v2.json（v2.0，9 类别 131 岗位；v1 存档 jobs0806.json）。

    输出扁平标签（含 level=所属类别 code，供论文提及识别对片段分类；
    v1 文件的 level 字段兼容回退）。
    """
    path = config.JOB_TAXONOMY
    data = json.load(open(path, encoding="utf-8"))
    labels = [{"code": d["code"], "name_zh": d["name_zh"], "name_en": d.get("name_en", ""),
               "level": d.get("level") or d.get("category")}
              for d in data.get("detail", {}).values()]
    return Taxonomy(labels, "job", data.get("system_name", "岗位体系"))


def load(mode):
    """按模式加载体系。mode: 'skill' | 'task' | 'job'"""
    mode = mode.lower()
    if mode == "skill":
        return load_skills()
    if mode == "task":
        return load_tasks()
    if mode == "job":
        return load_jobs()
    raise ValueError(f"未知 mode: {mode}（应为 skill / task / job）")
