# -*- coding: utf-8 -*-
"""LLM 调用封装：批量分类子句 → 技能/任务。

优化：
- 禁用推理（thinking disabled）提速
- 批量送入多句子，单次 LLM 调用摊销成本
- 重试 + 退避
- 稳健 JSON 解析（容错模型输出）
"""
import json
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import config
import prompts


class LLMClient:
    def __init__(self, api_key=None, model=None):
        # api_key 显式传入 → 整个客户端固定用该 key；缺省 → 每次请求经 KeyRing
        # 轮转（settings llm.api_keys_parallel > 1 时多账号分摊限速），重试换下一个
        self.api_key = api_key
        self.model = model or config.DEFAULT_MODEL
        self.total_tokens = 0
        self.call_count = 0
        self.concurrency = config.concurrency_total()    # 总批次并发 = 单key并发 × 启用key数
        self._lock = threading.Lock()                  # 保护 total_tokens/call_count 并发更新
        if api_key is None and not config.load_api_keys():
            raise RuntimeError("未找到 API key（codes/api-key.txt 或环境变量）")

    def _post(self, prompt):
        body_obj = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": config.MAX_TOKENS,
            "temperature": 0,
            "thinking": {"type": "disabled"},  # 禁用推理，显著提速
        }
        body = json.dumps(body_obj).encode("utf-8")
        last_err = None
        for attempt in range(config.RETRIES):
            key = self.api_key
            if key is None:
                import llm as _llm
                key = _llm.key_ring().next()      # 重试也换 key（限速错开账号）
            req = urllib.request.Request(
                config.API_URL, data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=config.TIMEOUT) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                usage = resp.get("usage", {})
                with self._lock:                       # 并发下计数安全
                    self.total_tokens += usage.get("total_tokens", 0)
                    self.call_count += 1
                content = resp["choices"][0]["message"].get("content", "")
                return self._extract_json_array(content)
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in (429, 500, 502, 503):
                    time.sleep((2 ** attempt) * 2 + random.uniform(0, 2))   # 指数退避 + 抖动
                    continue
                raise
            except Exception as e:
                last_err = str(e)
                time.sleep((2 ** attempt) * 2 + random.uniform(0, 2))
        raise RuntimeError(f"LLM 调用失败（{config.RETRIES} 次重试后）：{last_err}")

    @staticmethod
    def _extract_json_array(text):
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise ValueError(f"响应中未找到 JSON 数组: {text[:150]!r}")
        return json.loads(text[start:end + 1])

    def classify_sentences(self, sentences, taxonomy):
        """批量分类子句（JD 场景）。

        返回 {sentence: [{"code": ..., "skillpoints": [...]}]}
        - skill 模式：含技能点（skillpoints）
        - task 模式：不含技能点（skillpoints 空）
        """
        return self.classify_with(sentences, taxonomy, prompts.get_prompt(taxonomy.mode))

    def _gather(self, batches, run_batch):
        """并发跑各批次 → 顺序合并结果（线程内跑 _post+解析，主线程合并；缓存写入由调用方顺序做）。

        批级容错：单批失败（重试耗尽/解析失败）不中断整体——该批单元返回空且不写缓存，
        下次运行自动重试（窗口级大批量运行时，单批瞬时故障不应作废整窗已花掉的调用）。
        """
        result = {}
        lock = threading.Lock()
        n_fail = [0]

        def safe_batch(batch):
            try:
                return run_batch(batch)
            except Exception as e:
                with lock:
                    n_fail[0] += 1
                    if n_fail[0] <= 3 or n_fail[0] % 20 == 0:
                        print(f"[llm] 批失败（{len(batch)} 单元，第 {n_fail[0]} 次，不缓存待重试）：{e}")
                return {}
        if self.concurrency > 1 and len(batches) > 1:
            with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
                for out in ex.map(safe_batch, batches):
                    result.update(out)
        else:
            for batch in batches:
                result.update(safe_batch(batch))
        return result

    def classify_with(self, units, taxonomy, prompt_template):
        """按自定义提示词模板批量分类片段（JD 句子 / 论文文本片段通用）。

        prompt_template：含 {labels} 与 {sentences} 占位符的模板（如 paper_prompts 的论文提及提示词）。
        返回 {unit: [{"code": ..., "skillpoints": [...]}]}，字段 skills / tasks / jobs 均可识别。
        批次按 llm.concurrency 并发（提速；不改变调用数/token）。
        """
        batches = [units[i:i + config.BATCH_SIZE] for i in range(0, len(units), config.BATCH_SIZE)]

        def run_batch(batch):
            prompt = prompt_template.replace("{labels}", taxonomy.label_text()) \
                                    .replace("{sentences}", json.dumps(batch, ensure_ascii=False))
            entries = self._post(prompt)
            out = {}
            for e in entries:
                s = e.get("sentence", "")
                if s not in batch:
                    for b in batch:              # 模型可能轻微改写句子，模糊匹配
                        if b in s or s in b:
                            s = b
                            break
                    else:
                        continue
                items = e.get("skills") or e.get("tasks") or e.get("jobs") or []
                matches, seen = [], set()
                for it in items:
                    code = it.get("code") or taxonomy.name_to_code.get(it.get("name", ""))
                    if not code or code not in taxonomy.code_to_name or code in seen:
                        continue
                    seen.add(code)
                    sp = it.get("skillpoints") or []
                    if isinstance(sp, str):
                        sp = [sp]
                    matches.append({"code": code, "skillpoints": [p for p in sp if p]})
                out[s] = matches
            return out

        return self._gather(batches, run_batch)

    def stats(self):
        return {"calls": self.call_count, "tokens": self.total_tokens}

    def classify_merged(self, sentences, skill_tax, task_tax, overlay_labels=None):
        """合并模式：一句一次同时分类技能(含技能点)+任务+叠层候选。

        返回 {sentence: {"skills": [{"code","skillpoints"}], "tasks": [code, ...],
                         "overlays": [名称, ...]}}。替代 skill/task 两次分离调用（句级
        调用减半、不损穷举性）。批次按 llm.concurrency 并发。overlay_labels：叠层候选
        文本块（名称+定义清单，None/空 → 提示词填"（无）"）；overlays 按注入的候选
        名称集过滤（LLM 回显的未知名称丢弃）。
        """
        batches = [sentences[i:i + config.BATCH_SIZE] for i in range(0, len(sentences), config.BATCH_SIZE)]
        overlay_text = overlay_labels or "（无）"
        overlay_names = overlay_label_names(overlay_labels)

        def run_batch(batch):
            prompt = (prompts.PROMPT_MERGED.replace("{skill_labels}", skill_tax.label_text())
                      .replace("{task_labels}", task_tax.label_text())
                      .replace("{overlay_labels}", overlay_text)
                      .replace("{sentences}", json.dumps(batch, ensure_ascii=False)))
            entries = self._post(prompt)
            out = {}
            for e in entries:
                s = e.get("sentence", "")
                if s not in batch:
                    for b in batch:
                        if b in s or s in b:
                            s = b
                            break
                    else:
                        continue
                skills, seen_sk = [], set()
                for it in (e.get("skills") or []):
                    code = it.get("code") or skill_tax.name_to_code.get(it.get("name", ""))
                    if not code or code not in skill_tax.code_to_name or code in seen_sk:
                        continue
                    seen_sk.add(code)
                    sp = it.get("skillpoints") or []
                    if isinstance(sp, str):
                        sp = [sp]
                    skills.append({"code": code, "skillpoints": [p for p in sp if p]})
                tasks, seen_tk = [], set()
                for it in (e.get("tasks") or []):
                    code = it.get("code") or task_tax.name_to_code.get(it.get("name", ""))
                    if not code or code not in task_tax.code_to_name or code in seen_tk:
                        continue
                    seen_tk.add(code)
                    tasks.append(code)
                overlays, seen_ov = [], set()
                for nm in (e.get("overlays") or []):
                    if not isinstance(nm, str):
                        continue
                    # 模型可能回显带类型后缀的名称（"X（任务）"），剥后缀再对名集
                    base = nm
                    for suf in ("（任务）", "（技能）", "（岗位）"):
                        if base.endswith(suf):
                            base = base[:-len(suf)]
                            break
                    if base and base in overlay_names and base not in seen_ov:
                        seen_ov.add(base)
                        overlays.append(base)
                out[s] = {"skills": skills, "tasks": tasks, "overlays": overlays}
            return out

        return self._gather(batches, run_batch)


def overlay_label_names(overlay_labels):
    """从叠层候选文本块提取候选名称集合（供 overlays 回显过滤）。

    文本行格式 "- 名称（任务/技能/岗位）：定义"（build_overlay_labels 产出）。
    """
    names = set()
    if not overlay_labels:
        return names
    for line in overlay_labels.splitlines():
        line = line.strip()
        if line.startswith("- ") and "（" in line:
            nm = line[2:line.index("（")].strip()
            if nm:
                names.add(nm)
    return names
