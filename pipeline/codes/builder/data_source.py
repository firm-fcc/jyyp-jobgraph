# -*- coding: utf-8 -*-
"""数据源抽象与工厂。

设计目标：Builder 不关心数据来自哪里（JD / 论文 / 新闻 / 简历），只通过统一接口获取文档。
- DataSource：抽象基类，提供 sample / next_batch / remaining（供热更新迭代）
- StratifiedSampler（sampler.py）：通用分层抽样引擎，各数据源按其"层"语义注入条目
- JDDataSource：JD 实现，从 data/jd_dataset/*.csv 读取 job_information，按岗位大类分层
- register_data_source / make_data_source：注册表与工厂，为未来数据源预留

未来接入新闻/论文/简历时，实现一个 DataSource（可直接复用 StratifiedSampler 引擎，
传入合适的条目与层定义），再 register_data_source("news", NewsDataSource) 即可，
Builder 逻辑不变。参考分层：论文按专题 / S-A-B-C 分档 / 时间窗；新闻按来源 / A-B-C 相关度分级。
"""
import csv
import json
import os
import re
from abc import ABC, abstractmethod

import config
from sampler import StratifiedSampler


class DataSource(ABC):
    """数据源统一接口。"""

    @abstractmethod
    def sample(self, n):
        """随机采样 n 条文档。"""
        pass

    @abstractmethod
    def next_batch(self, n):
        """取下一批 n 条文档（迭代推进，供热更新使用）。"""
        pass

    @abstractmethod
    def remaining(self):
        """剩余未消费的文档数。"""
        pass


class JDDataSource(StratifiedSampler, DataSource):
    """JD 数据源：按岗位大类分层抽样（含 IT 过滤）。

    分层依据（v2，2026-08-21 起）：JD 的 funtype part → jobs_v2.json 岗位（norm_part 规范化
    匹配，与 build_jobs 同一口径）→ 岗位所属 v2 一级类别（9 类）。**兼作 IT 过滤**：任一
    part 命中 v2 岗位才保留该行，全部未命中（含无 funtype）→ 视为非 IT，直接跳过。

    旧口径（funtype_it_map.json → jobs0806 一级大类）有两个问题：① 映射文件已随
    2026-08-19 数据事故丢失，静默退化为全量"其他"层（分层失效）；② 不做任何 IT 过滤，
    数据集内混入的非 IT JD（化学检验/机械设计/供应链/法务等）进入冷启动样本——这是
    v0.1 任务体系含 13 个非 IT 任务（化学分析与实验检测/机械设计与自动化等）的根因。
    """

    JOBS_V2_PATH = os.path.join(config.PROJECT_ROOT, "classify", "Jobs", "jobs_v2.json")

    def __init__(self, csv_dir=None, seed=42, dedup=True):
        self.csv_dir = csv_dir or config.JD_CSV_DIR
        self.funtype2top = self._build_funtype_top_map()
        super().__init__(loader=self._load_items, seed=seed, dedup=dedup)

    # ---------------- 分层映射 ----------------
    @staticmethod
    def _norm_part(s):
        """funtype part 规范化（镜像 build_jobs.norm_part 口径，仅匹配比对用）。"""
        s = (s or "").strip().lower().replace(" ", "")
        out = []
        for ch in s:
            code = ord(ch)
            if code == 0xFF08:      # （
                ch = "("
            elif code == 0xFF09:    # ）
                ch = ")"
            elif 0xFF01 <= code <= 0xFF5E:  # 全角ascii区
                ch = chr(code - 0xFEE0)
            out.append(ch)
        return re.sub(r"\.{2,}|…{1,}", "等", "".join(out))

    def _build_funtype_top_map(self):
        """构建 funtype part（规范化）→ v2 一级类别名 的映射。"""
        funtype2top = {}
        try:
            with open(self.JOBS_V2_PATH, encoding="utf-8") as f:
                jobs = json.load(f)
            cat_name = {c["code"]: c["name_zh"] for c in jobs.get("categories", [])}
            for d in jobs.get("detail", {}).values():
                cat = cat_name.get(d.get("category"), d.get("category", ""))
                for ft in d.get("funtypes") or []:
                    funtype2top[self._norm_part(ft)] = cat
        except Exception as e:
            raise RuntimeError(
                f"[jd] 加载 jobs_v2 失败: {e}（funtype→v2 映射是采样与 IT 过滤的前置条件，"
                f"请检查 {self.JOBS_V2_PATH}）")
        if not funtype2top:
            raise RuntimeError("[jd] jobs_v2 无任何 funtype 挂载，将过滤掉全部 JD，中止")
        return funtype2top

    def _stratum_of(self, funtype):
        """funtype → 层名；任一 part 命中 v2 岗位即返回其类别，否则 None（非 IT，丢弃）。"""
        parts = [p.strip() for p in re.split(r"\s+or\s+", funtype or "") if p.strip()]
        for p in parts:
            top = self.funtype2top.get(self._norm_part(p))
            if top:
                return top
        return None

    def _load_items(self):
        """读取 JD CSV，产出 (stratum, text) 条目（IT 过滤在此完成；截断/去重由引擎处理）。"""
        if not os.path.isdir(self.csv_dir):
            print(f"[jd] 数据目录不存在: {self.csv_dir}")
            return
        for f in sorted(os.listdir(self.csv_dir)):
            if not f.endswith(".csv"):
                continue
            with open(os.path.join(self.csv_dir, f), encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                cols = reader.fieldnames or []
                col = "job_information" if "job_information" in cols else "job"
                for row in reader:
                    text = row.get(col, "")
                    if not text or len(text.strip()) < 10:  # 粗过滤，引擎内再精确过滤
                        continue
                    stratum = self._stratum_of(row.get("funtype", ""))
                    if stratum is None:  # funtype 未命中任何 v2 岗位 → 非 IT，跳过
                        continue
                    yield stratum, text


# ---------------- 工厂与注册表 ----------------
_DATA_SOURCE_REGISTRY = {
    "jd": JDDataSource,
    # 预留（实现后在此注册即可，Builder 逻辑不变）：
    # "news": NewsDataSource,     # 按来源 / 相关度 A-B-C 分级分层
    # "paper": PaperDataSource,   # 按专题 / S-A-B-C 分档 / 时间窗分层
}


def register_data_source(kind, cls):
    """注册新的数据源实现（供未来 news / paper / resume 接入）。"""
    if not (kind and isinstance(kind, str)):
        raise ValueError("数据源类型 kind 必须为非空字符串")
    _DATA_SOURCE_REGISTRY[kind] = cls


def make_data_source(kind="jd", **kwargs):
    """数据源工厂：按 kind 创建（jd 当前可用；news/paper 预留）。"""
    cls = _DATA_SOURCE_REGISTRY.get(kind)
    if cls is None:
        raise ValueError(
            f"未知数据源类型: {kind}（当前支持: {', '.join(sorted(_DATA_SOURCE_REGISTRY))}）")
    return cls(**kwargs)
