# -*- coding: utf-8 -*-
"""热更新引擎：propose → supervise → apply 迭代循环（可复用）。

本引擎不绑定任务体系——通过注入 propose_fn / supervise_fn / apply_fn，
可用于任务体系构建，也可复用于**图谱更新**等其他结构化产物的迭代精化。

流程（每轮消费一批数据，按子块分批交给 LLM）：
1. propose：分析一个子块（chunk）的 JD，对照当前产物提出更新提案
2. supervise：监督 Agent 判断提案必要性（精简原则，语义接近即同任务）
3. apply：应用被批准的更新
4. 重检：确认该子块数据是否已覆盖；未覆盖则在同一子块上循环精化（防丢批）
终止：数据源耗尽 或 达到最大轮数。

子块化：200 条一批全部塞进一个 prompt 上下文过长（约 24 万字符）会加剧 LLM 注意力问题，
故每轮按 HOT_CHUNK 拆成若干子块，每次只把一小部分 JD 交给 LLM。
"""
import json
import math

import config


class HotUpdater:
    def __init__(self, taxonomy_store, propose_fn, supervise_fn, apply_fn, logger=None):
        """
        taxonomy_store: 带 load/save/to_labels/tasks 的对象（被精化的产物）
        propose_fn(documents, store) -> {"covered": bool, "updates": [...]}
        supervise_fn(proposal, store) -> (approved_updates, rejected_reasons)
        apply_fn(store, approved_updates) -> log: list[str]
        logger: 可选 RunLogger，记录提案/监督/应用/重检全过程（人类可读 + 结构化）
        """
        self.store = taxonomy_store
        self.propose_fn = propose_fn
        self.supervise_fn = supervise_fn
        self.apply_fn = apply_fn
        self.logger = logger

    def run(self, data_source, max_rounds=None, batch_size=None, max_recheck=None,
            checkpoint_path=None, chunk_size=None):
        """执行多轮热更新。返回运行日志。

        batch_size: 每轮消费文档数，按 chunk_size 拆成若干子块分别交给 LLM（控制上下文长度）。
        chunk_size: 单次提案交给 LLM 的文档数（默认 config.HOT_CHUNK）。
        max_recheck: 同一子块 propose→supervise→apply 循环精化的重检上限。
        checkpoint_path: 若提供，每消费一个子块即把已消费 md5 落盘（断点继续）；
                         中断后下次 run 恢复 consumed，仅丢失当前未完成的子块。
        """
        max_rounds = max_rounds or config.MAX_ROUNDS
        batch_size = batch_size or config.HOT_BATCH
        chunk_size = chunk_size or config.HOT_CHUNK
        max_recheck = max_recheck or config.MAX_RECHECK
        chunks_per_round = math.ceil(batch_size / chunk_size)
        logs = []

        for r in range(max_rounds):
            for c in range(chunks_per_round):
                if data_source.remaining() <= 0:
                    logs.append(f"round{r+1}/子块{c+1}: 数据源已耗尽，停止")
                    if self.logger:
                        self.logger.note(f"round{r+1}/子块{c+1}: 数据源已耗尽，停止")
                    return logs
                take = min(chunk_size, batch_size - c * chunk_size)  # 末子块取该轮余量
                chunk = data_source.next_batch(take)
                # 消费子块后立即落盘断点：中断后下次从下一子块继续
                if checkpoint_path and hasattr(data_source, "save_checkpoint"):
                    data_source.save_checkpoint(checkpoint_path)
                logs.append(f"round{r+1}/子块{c+1}: 投喂 {len(chunk)} 条")
                if self.logger:
                    self.logger.hot_round_start(r + 1, len(chunk), chunk=c + 1)
                self._process_chunk(chunk, r + 1, max_recheck, logs)
                if self.logger:
                    self.logger.hot_round_end(r + 1, len(self.store.tasks()), chunk=c + 1)

        return logs

    @staticmethod
    def _dedupe_updates(updates):
        """同一批提案去重：add 按任务名、merge 按 codes 组合、modify 按目标 code。"""
        seen = set()
        out = []
        for u in updates:
            if not isinstance(u, dict):
                continue
            action = u.get("action")
            if action == "add":
                key = ("add", (u.get("task") or {}).get("name_zh", ""))
            elif action == "merge":
                key = ("merge", tuple(sorted(u.get("merge_codes") or [])))
            elif action == "modify":
                key = ("modify", u.get("target_code") or (u.get("task") or {}).get("code", ""))
            else:
                key = (str(action), json.dumps(u, ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            out.append(u)
        return out

    def _process_chunk(self, batch, round_no, max_recheck, logs):
        """对同一子块反复 propose→supervise→apply，直至完全覆盖或达到重检上限。

        原实现中子块一经 next_batch 消费，重检未覆盖的内容就随批丢失（不会再投喂）；
        这里在同一子块上循环精化，保证子块内未覆盖内容被继续处理，而不是静默丢弃。
        """
        for attempt in range(1, max_recheck + 1):
            # 1) 提案（批内去重 + 过滤与现有体系同名的 add）
            proposal = self.propose_fn(batch, self.store)
            raw_updates = proposal.get("updates", [])
            updates = self._dedupe_updates(raw_updates)
            if len(updates) < len(raw_updates):
                logs.append(f"round{round_no}: 提案去重 {len(raw_updates)} → {len(updates)} 项")
            # LLM 在长上下文下会重提已有任务（含本批刚新增的）；程序化剔除同名 add，不依赖 LLM 自觉
            existing = {t["name_zh"] for t in self.store.tasks()}
            kept = [u for u in updates
                    if not (u.get("action") == "add"
                            and (u.get("task") or {}).get("name_zh", "").strip() in existing)]
            if len(kept) < len(updates):
                logs.append(f"round{round_no}: 过滤 {len(updates) - len(kept)} 条与现有体系同名的 add")
            updates = kept
            proposal["updates"] = updates   # 关键：监督读 proposal["updates"]，必须写回过滤结果
            if self.logger:
                self.logger.propose(round_no, attempt, proposal.get("covered", False), updates)
            if proposal.get("covered") and not updates:
                logs.append(f"round{round_no}: 本批已被当前体系覆盖，无需更新")
                return
            if not updates:
                logs.append(f"round{round_no}: 本批提案均为已有任务的重复（体系已覆盖），收敛")
                return

            # 2) 监督
            approved, rejected = self.supervise_fn(proposal, self.store)
            if self.logger:
                self.logger.supervise(round_no, attempt, approved, rejected)
            if not approved:
                logs.append(f"round{round_no}: 提案{len(updates)}项全部被监督拒绝（体系已精简），收敛")
                return

            # 3) 应用
            apply_log = self.apply_fn(self.store, approved)
            self.store.save()
            if self.logger:
                self.logger.apply(round_no, attempt, apply_log)
            logs.append(f"round{round_no}: 应用 {len(approved)} 项更新 -> {apply_log}")
            # 若批准的更新全部被同名防重跳过，说明体系已实质覆盖，直接收敛（防重检空转）
            if not any(not x.startswith("skip ") for x in apply_log):
                logs.append(f"round{round_no}: 新增均为重复项（体系已覆盖），收敛")
                return

            # 4) 重检本批覆盖
            recheck = self.propose_fn(batch, self.store)
            if self.logger:
                self.logger.recheck(round_no, attempt, recheck.get("covered", False))
            if recheck.get("covered"):
                logs.append(f"round{round_no}: 重检通过，本批已覆盖")
                return
            logs.append(f"round{round_no}: 重检仍有余量（第 {attempt} 次），继续精化")

        logs.append(f"round{round_no}: 达到重检上限 {max_recheck}，本批剩余内容转人工/后续处理")
        if self.logger:
            self.logger.note(f"round{round_no}: 达到重检上限 {max_recheck}，本批剩余内容转人工/后续处理")
