# -*- coding: utf-8 -*-
"""体系存储与读写（通用 taxonomy 容器，task / skill 两模式）。

- 读取/写入体系 JSON
  - task 模式：{system_name, version, date, tasks:[{code,name_zh,name_en,description}]}
  - skill 模式：{system_name, version, date, total, detail:{code:{code,name_zh,name_en,definition,skill_type}}}
    （detail 结构兼容 Extractor 的 load_skills()）
- to_labels()：生成提示词用的标签文本
- 供 cold_start / hot_update / apply 共享
"""
import json
import os

import config


class TaxonomyStore:
    def __init__(self, path=None, mode="task", system_name=None):
        """mode: 'task' | 'skill'。"""
        self.mode = mode
        self.path = path or (config.SKILL_BUILDER_OUTPUT if mode == "skill" else config.TASK_TAXONOMY)
        if mode == "skill":
            self.system_name = system_name or "技能体系（Builder 构建）"
            self.data = {
                "system_name": self.system_name, "version": "0.1", "date": "2026-08-07",
                "source": "Builder", "total": 0, "detail": {},
            }
        else:
            self.system_name = system_name or "任务体系（Task 层）"
            self.data = {
                "system_name": self.system_name, "version": "0.1", "date": "2026-08-07",
                "source": "Builder", "tasks": [],
            }

    # ---------- 读写 ----------
    def load(self, path=None):
        p = path or self.path
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                self.data = json.load(f)
            # 兼容：加载后按内容推断 mode（skill 体系有 detail 字段）
            if "detail" in self.data and "tasks" not in self.data:
                self.mode = "skill"
        return self

    def save(self, path=None):
        p = path or self.path
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if self.mode == "skill":
            self.data["total"] = len(self.tasks())
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        return self

    # ---------- 访问 ----------
    def tasks(self):
        """返回条目列表（task: tasks 数组；skill: detail 的 value 列表）。"""
        if self.mode == "skill":
            return list(self.data.setdefault("detail", {}).values())
        return self.data.setdefault("tasks", [])

    def to_labels(self):
        """生成提示词标签文本。"""
        return "\n".join(f"{t['code']}:{t['name_zh']}" for t in self.tasks())

    def next_code(self):
        """生成下一个条目编码 T-NN / S-NN。"""
        prefix = "S" if self.mode == "skill" else "T"
        used = set()
        for t in self.tasks():
            code = t.get("code", "")
            if code.startswith(prefix + "-") and code.split("-")[-1].isdigit():
                used.add(int(code.split("-")[-1]))
        n = 1
        while n in used:
            n += 1
        return f"{prefix}-{n:02d}"

    # ---------- 增 / 并 / 改 ----------
    def add_task(self, name_zh, name_en="", description="", skill_type=None):
        """新增条目。task: description 字段；skill: definition + skill_type 字段。"""
        code = self.next_code()
        if self.mode == "skill":
            task = {"code": code, "name_zh": name_zh, "name_en": name_en or "",
                    "definition": description or "", "skill_type": skill_type or "hard"}
            self.data["detail"][code] = task
            self.data["total"] = len(self.data["detail"])
        else:
            task = {"code": code, "name_zh": name_zh, "name_en": name_en or "",
                    "description": description or ""}
            self.tasks().append(task)
        return task

    def merge_tasks(self, code_a, code_b):
        """将 code_b 合并入 code_a（保留 a，移除 b）。"""
        tasks = self.tasks()
        a = next((t for t in tasks if t.get("code") == code_a), None)
        b = next((t for t in tasks if t.get("code") == code_b), None)
        if not a or not b or code_a == code_b:
            return False
        key = "definition" if self.mode == "skill" else "description"
        if b.get(key):
            a[key] = (a.get(key, "") + "；" + b[key]).strip("；")
        if self.mode == "skill":
            self.data["detail"].pop(code_b, None)
            self.data["total"] = len(self.data["detail"])
        else:
            self.tasks().remove(b)
        return True

    def modify_task(self, code, name_zh=None, name_en=None, description=None, skill_type=None):
        t = next((x for x in self.tasks() if x.get("code") == code), None)
        if not t:
            return False
        if name_zh is not None:
            t["name_zh"] = name_zh
        if name_en is not None:
            t["name_en"] = name_en
        if description is not None:
            if self.mode == "skill":
                t["definition"] = description
            else:
                t["description"] = description
        if skill_type is not None and self.mode == "skill":
            t["skill_type"] = skill_type
        return True
