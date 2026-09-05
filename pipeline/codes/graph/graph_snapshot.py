# -*- coding: utf-8 -*-
"""图谱时间截面快照读取 API：`GraphSnapshot.load(window)` 加载一个时间截面。

磁盘保持**原样拷贝**（基图节点 = 体系 JSON 原样），归一化放在读取层：
- `nodes(layer, kind)`：统一为 `[{id, name_zh, name_en, ...}]`
- `node_index()`：全局 id → {layer, kind, node}（解边端点；基图/叠层 id 空间不冲突：
  岗位 A0xx/7xxx、任务 T-、技能 S-、叠层 PJ-/PT-/PS-/PK-）
- `base_labels()`：对齐 `taxonomy_mapper.load_base_labels` 的 {tasks, skills, jobs} 形态

容缺加载：缺文件置空并记入 `missing`，供 `validate()` 汇总。
"""
import json
import os

import graph_config as config
from snapshot_builder import parse_window


class GraphSnapshot:
    """一个时间截面的图谱快照（只读访问器）。"""

    # layer → kind → 文件名
    _FILES = {
        "base": {
            "jobs": "jobs.json", "tasks": "tasks.json", "skills": "skills.json", "skillpoints": "skillpoints.json",
            "job_task": "job_task.json", "job_skill": "job_skill.json",
            "task_skill": "task_skill.json", "skill_skillpoint": "skill_skillpoint.json",
        },
        "delta": {
            "new_jobs": "new_jobs.json", "new_tasks": "new_tasks.json",
            "new_skills": "new_skills.json", "skillpoints": "skillpoints.json",
            "strengthenings": "strengthenings.json", "job_links": "job_links.json",
        },
    }
    _NODE_KINDS = {
        "base": ("jobs", "tasks", "skills", "skillpoints"),
        "delta": ("new_jobs", "new_tasks", "new_skills", "skillpoints"),
    }

    def __init__(self, window, root=None):
        self.window = window
        self.root = root or config.GRAPH_ROOT
        self.path = os.path.join(self.root, window)
        self._meta = {}
        self._data = {"base": {}, "delta": {}}
        self.missing = []
        self.period_start = self.period_end = None
        self._node_index = None
        self._load()

    # ---------------- 加载 ----------------
    def _load(self):
        meta_path = os.path.join(self.path, config.META_FILENAME)
        if os.path.exists(meta_path):
            try:
                self._meta = json.load(open(meta_path, encoding="utf-8"))
            except Exception as e:
                self.missing.append(f"meta.json 解析失败: {e}")
        for p in ("period_start", "period_end"):
            v = self._meta.get(p)
            if v:
                try:
                    from datetime import date
                    setattr(self, p, date.fromisoformat(v))
                except ValueError:
                    pass
        for layer in ("base", "delta"):
            sub = os.path.join(self.path, layer)
            for kind, fname in self._FILES[layer].items():
                p = os.path.join(sub, fname)
                if not os.path.exists(p):
                    self.missing.append(f"{layer}/{kind}: 缺文件 {p}")
                    self._data[layer][kind] = None
                    continue
                try:
                    self._data[layer][kind] = json.load(open(p, encoding="utf-8"))
                except Exception as e:
                    self.missing.append(f"{layer}/{kind}: 解析失败 {e}")
                    self._data[layer][kind] = None

    @classmethod
    def load(cls, window, root=None):
        """加载指定窗口的截面。窗口不存在时返回含 missing 记录的空快照。"""
        return cls(window, root)

    # ---------------- 元数据 ----------------
    def meta(self):
        return self._meta

    def period(self):
        return self.period_start, self.period_end

    def stats(self):
        return dict(self._meta.get("stats", {}))

    # ---------------- 节点 ----------------
    @staticmethod
    def _as_node(raw, id_):
        """归一化节点：确保 id/name_zh/name_en 键存在。"""
        node = dict(raw)
        node["id"] = node.get("code", node.get("id", id_))
        node.setdefault("name_zh", "")
        node.setdefault("name_en", "")
        return node

    def nodes(self, layer, kind):
        """归一化节点列表：[{id, name_zh, name_en, ...}]。缺失返回 []。"""
        fd = self._data.get(layer, {}).get(kind)
        if fd is None:
            return []
        if kind in ("jobs", "skills"):
            detail = fd.get("detail", {}) if isinstance(fd, dict) else {}
            return [self._as_node(e, code) for code, e in detail.items() if isinstance(e, dict)]
        if kind == "tasks":
            arr = fd.get("tasks", []) if isinstance(fd, dict) else []
            return [self._as_node(t, t.get("code", "")) for t in arr if isinstance(t, dict)]
        if isinstance(fd, dict) and "items" in fd:
            return [self._as_node(it, it.get("id", "")) for it in fd.get("items", []) if isinstance(it, dict)]
        if isinstance(fd, dict) and "skillpoints" in fd:  # 基图技能点 dict 形态
            sp = fd.get("skillpoints", {})
            return [self._as_node({"name_zh": k, **(v if isinstance(v, dict) else {})}, k)
                    for k, v in sp.items()]
        return []

    def base_labels(self):
        """对齐 taxonomy_mapper.load_base_labels 形态：{tasks, skills, jobs}。"""
        return {
            "tasks": [{"code": n["id"], "name_zh": n["name_zh"], "name_en": n["name_en"]}
                      for n in self.nodes("base", "tasks")],
            "skills": [{"code": n["id"], "name_zh": n["name_zh"], "name_en": n["name_en"],
                        "skill_type": n.get("skill_type", "")}
                       for n in self.nodes("base", "skills")],
            "jobs": [{"code": n["id"], "name_zh": n["name_zh"], "name_en": n["name_en"]}
                     for n in self.nodes("base", "jobs")],
        }

    def node_index(self):
        """全局 id → {layer, kind, node}（基图 + 叠层）。"""
        if self._node_index is not None:
            return self._node_index
        idx = {}
        for layer, kinds in self._NODE_KINDS.items():
            for kind in kinds:
                for n in self.nodes(layer, kind):
                    idx[n["id"]] = {"layer": layer, "kind": kind, "node": n}
        self._node_index = idx
        return idx

    # ---------------- 边 ----------------
    def edges(self, kind, layer="base"):
        """某种边的列表（缺失返回 []）。"""
        fd = self._data.get(layer, {}).get(kind)
        if not isinstance(fd, dict):
            return []
        return fd.get("edges", [])

    def strengthenings(self):
        """叠层增强记录（对基图条目的强度增强）。"""
        fd = self._data.get("delta", {}).get("strengthenings")
        if not isinstance(fd, dict):
            return []
        return fd.get("items", [])

    def job_links(self):
        """叠层岗位关联边（新岗位→任务/技能）。"""
        return self.edges("job_links", "delta")

    # ---------------- 附属与合成层（容缺读取） ----------------
    def entity_freq(self):
        """基图实体文档频率 E_jd（base/entity_freq.json，base_builder 产物）。

        缺失/损坏返回 None（调用方按 E_jd=0 处理并自行告警）。
        """
        p = os.path.join(self.path, config.BASE_SUBDIR, config.BASE_AUX_FILENAMES["entity_freq"])
        if not os.path.exists(p):
            return None
        try:
            return json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def effective_edges(self, kind):
        """合成层（effective/）某种边的列表；未合成/缺文件返回 []。"""
        p = os.path.join(self.path, config.EFFECTIVE_SUBDIR, config.EDGE_FILENAMES[kind])
        if not os.path.exists(p):
            return []
        try:
            return json.load(open(p, encoding="utf-8")).get("edges", [])
        except (OSError, ValueError):
            return []

    # ---------------- 汇总与校验 ----------------
    def summary(self):
        return {
            "window": self.window,
            "period": [self.period_start.isoformat() if self.period_start else "",
                       self.period_end.isoformat() if self.period_end else ""],
            "stats": self.stats(),
            "missing": list(self.missing),
        }

    def validate(self):
        """结构校验：返回错误清单（空 = 通过）。"""
        errs = []
        idx = self.node_index()
        for layer in ("base", "delta"):
            for kind, fd in self._data[layer].items():
                if not isinstance(fd, dict):
                    continue
                for arr_key in ("edges", "items"):
                    if arr_key in fd:
                        arr = fd.get(arr_key) or []
                        if fd.get("total") != len(arr):
                            errs.append(f"{layer}/{kind}: total={fd.get('total')} != 实际 {len(arr)}")
        for kind in config.BASE_EDGE_KINDS:
            for e in self.edges(kind, "base"):
                if e.get("src") not in idx:
                    errs.append(f"base/{kind}: src {e.get('src')} 无对应节点")
                if e.get("dst") not in idx:
                    errs.append(f"base/{kind}: dst {e.get('dst')} 无对应节点")
        for e in self.job_links():
            if e.get("src") not in idx:
                errs.append(f"delta/job_links: src {e.get('src')} 无对应节点")
            if e.get("dst") not in idx:
                errs.append(f"delta/job_links: dst {e.get('dst')} 悬空（无对应节点）")
        for s in self.strengthenings():
            if s.get("code") and s.get("code") not in idx:
                errs.append(f"delta/strengthenings: {s.get('taxonomy')}:{s.get('code')} 在基图/叠层中不存在")
        return errs

    # ---------------- 枚举 ----------------
    @staticmethod
    def list_slices(root=None):
        """列出根目录下所有时间截面（按 parse_window sort_key 排序，两粒度可混排）。"""
        root = root or config.GRAPH_ROOT
        out = []
        if not os.path.isdir(root):
            return out
        for name in os.listdir(root):
            if not os.path.isdir(os.path.join(root, name)):
                continue
            try:
                _, _, _, sort_key = parse_window(name)
            except ValueError:
                continue
            out.append((sort_key, name))
        return [name for _, name in sorted(out)]
