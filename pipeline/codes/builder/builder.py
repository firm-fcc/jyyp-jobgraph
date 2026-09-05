# -*- coding: utf-8 -*-
"""Builder 编排器：冷启动 + 热更新 主流程（供外部/Agent 调用）。

    builder = Builder(source="jd")
    builder.cold_start()          # 冷启动：归纳初始任务体系
    builder.hot_update()          # 热更新：多轮迭代完善
    # 或 builder.full() 一步完成
"""
import functools
import os

import config
import cold_start as cold
import apply as apply_mod
import propose as propose_mod
import supervisor as sup_mod
from data_source import make_data_source
from hot_update import HotUpdater
from logger import RunLogger
from taxonomy_store import TaxonomyStore


class Builder:
    def __init__(self, source="jd", mode="task", taxonomy_path=None, log_path=None,
                 **source_kwargs):
        """mode: 'task'（任务体系）| 'skill'（技能体系）。"""
        self.source_name = source
        self.mode = mode
        # 数据源经工厂创建：jd 为分层抽样器；news/paper 注册到 make_data_source 后自动接入
        self.data_source = make_data_source(source, **source_kwargs)
        self.taxonomy_store = TaxonomyStore(
            taxonomy_path or (config.SKILL_BUILDER_OUTPUT if mode == "skill" else config.TASK_TAXONOMY),
            mode=mode)
        # 运行跟踪日志：人类可读 md + 结构化 jsonl（--log 可改前缀）
        log_default = config.SKILL_BUILDER_LOG if mode == "skill" else config.BUILDER_LOG
        self.logger = RunLogger(jsonl_path=log_path or log_default)
        # 断点文件：与体系同目录（tasks.json -> tasks_checkpoint.json）
        self.checkpoint_path = os.path.splitext(self.taxonomy_store.path)[0] + "_checkpoint.json"

    # ---------- 冷启动 ----------
    def cold_start(self, n_samples=None):
        """采样数据，归纳初始体系（任务或技能）。"""
        n = n_samples or config.COLD_SAMPLE
        self.logger.run_start(self.source_name, "cold")
        # 冷启动重建体系：此前消费的数据不再视为已覆盖，清除旧断点
        if os.path.exists(self.checkpoint_path):
            os.remove(self.checkpoint_path)
        docs = self.data_source.sample(n)
        unit = "技能" if self.mode == "skill" else "任务"
        print(f"[cold] 采样 {len(docs)} 条文档，归纳初始{unit}体系...")
        cold.cold_start(docs, self.taxonomy_store, n, self.logger, mode=self.mode)
        print(f"[cold] 完成：{len(self.taxonomy_store.tasks())} 个{unit}")
        return self.taxonomy_store

    # ---------- 热更新 ----------
    def hot_update(self, rounds=None, batch_size=None, chunk_size=None, resume=True):
        """多轮热更新，完善任务体系。

        chunk_size: 每轮 batch_size 拆成若干子块，单次提案交给 LLM 的文档数（控制上下文长度）。
        resume=True（默认）：若存在断点文件，自动恢复已消费批次，从既有体系继续热更新；
        中断后重跑可跨进程继续，不必重复处理已覆盖批次。
        """
        if resume and os.path.exists(self.checkpoint_path):
            consumed = self.data_source.load_checkpoint(self.checkpoint_path)
            if consumed:
                self.data_source.restore_consumed(consumed)
                print(f"[hot] 断点恢复：已消费 {len(consumed)} 条，剩余 {self.data_source.remaining()} 条")
        self.logger.run_start(self.source_name, "hot")
        unit = "技能" if self.mode == "skill" else "任务"
        updater = HotUpdater(
            self.taxonomy_store,
            propose_fn=functools.partial(propose_mod.propose_updates, mode=self.mode),
            supervise_fn=functools.partial(sup_mod.supervise, mode=self.mode),
            apply_fn=apply_mod.apply_updates,
            logger=self.logger,
        )
        logs = updater.run(self.data_source, rounds, batch_size,
                           checkpoint_path=self.checkpoint_path, chunk_size=chunk_size)
        for line in logs:
            print(f"[hot] {line}")
        print(f"[hot] 完成：{len(self.taxonomy_store.tasks())} 个{unit}")
        return logs

    # ---------- 全流程 ----------
    def full(self, n_samples=None, rounds=None):
        """冷启动 + 热更新 一步完成。"""
        self.cold_start(n_samples)
        self.hot_update(rounds)
        return self.taxonomy_store
