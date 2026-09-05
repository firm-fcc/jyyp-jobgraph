# -*- coding: utf-8 -*-
"""skillpoint 三层归一：字面折叠（L1）→ 别名注册表（L2）→ LLM 首见归一（L3）。

目的：同一技术不因命名差异（大小写/分隔符/中英文/简称/俗称）在图谱中分裂成多个
skillpoint 节点；跨窗口/跨期 S-SP 边可比。

层次（deterministic-first，LLM 只判首见名）：
- L1 字面折叠（零成本）：norm_key = 去分隔符+小写（保留中英文与数字）。名字的 norm_key
  等于某 canonical 的 norm_key → 该 canonical（如 Mybatis/MyBatis→MyBatis、HTML 5→HTML5、
  NodeJS→Node.js）。
- L2 别名注册表（零成本）：codes/graph/skillpoint_registry.json 人工审定层——
  canonical + aliases + category（语言/框架/库/工具/平台/协议/数据库/中间件/方法/标准/硬件/系统）。
- L3 LLM 首见归一（每名终身一次，缓存跨窗复用）：L1/L2 未命中的名字按批送 LLM，
  只允许 merge(→已有 canonical) / new(登记新 canonical+类别) 两种动作；
  判定落 codes/graph/output/skillpoint_alias_cache.jsonl（可人工复审后提升进注册表）。

硬口径（注册表与 L3 prompt 同源）：只合并同一技术的命名变体；不同代际/组件/相邻技术
保持独立（Spring≠Spring Boot≠Spring Cloud、C≠C++≠C#、AngularJS≠Angular、CSS≠CSS3）。

对外 API：
    norm = SkillpointNormalizer(llm_post=ext.llm._post)   # 生产；测试注入 mock
    mapping = norm.resolve_batch(names)                    # {name: (canonical, category)}
    rec_map = norm.normalize_skillpoint_map(skillpoint_map)  # {skill: [canonical...]（去重保序）
"""
import json
import os
import re
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, "skillpoint_registry.json")
ALIAS_CACHE_PATH = os.path.join(HERE, "output", "skillpoint_alias_cache.jsonl")

CATEGORIES = ("语言", "框架", "库", "工具", "平台", "协议", "数据库",
              "中间件", "方法", "标准", "硬件", "系统", "办公")

# 类别判定规则（注入 L3 prompt；注册表 note 引用此处为唯一事实源）。
# 关键防歧义：标准=认证与管理体系，不含技术规范——CSS/JSON/USB 这类"规范"按技术属性归位。
CATEGORY_RULES = """- 语言=编程/标记/查询语言与数据格式（含 HTML、CSS、XML、JSON、Shader 这类规范式语言）
- 框架=应用开发框架（Spring Boot、Vue、Qt、MyBatis）
- 库=程序库/组件库（jQuery、NumPy、ElementUI、OpenCV）
- 工具=开发/调试/构建/版本/设计/运维监控类软件（Git、Maven、Webpack、Photoshop、Zabbix）
- 平台=容器编排、云计算与虚拟化、分布式计算引擎、运行时环境（Kubernetes、Hadoop、Spark、Node.js、CUDA）
- 协议=网络与通信协议/总线接口标准（TCP/IP、MQTT、USB、PCIe、CAN）
- 数据库=数据存储引擎（MySQL、Hive、HBase、ClickHouse）
- 中间件=消息/检索/应用服务器类中间件（Kafka、Elasticsearch、WebLogic）
- 方法=方法论/架构风格/编程模型（微服务、RESTful API、MapReduce、UML、ETL）
- 标准=认证与管理体系规范（CMMI、ISO27001、ITIL、等保）——不含技术规范：CSS/JSON/USB 虽是规范但按技术属性归入语言/协议/硬件
- 硬件=芯片/板卡/控制器/仪器（STM32、FPGA、PLC、DSP）
- 系统=操作系统/RTOS（Linux、Windows、FreeRTOS）
- 办公=办公软件（Word、Excel、PowerPoint）"""

_NORM_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]")
_LLM_BATCH = 50
_PROMPT_CANON_CAP = 400          # prompt 中携带的已有 canonical 数上限（按使用频次取头部）


def norm_key(s):
    """字面折叠键：小写 + 去分隔符（保留字母/数字/中文）。"""
    return _NORM_RE.sub("", (s or "").lower())


class SkillpointNormalizer:
    def __init__(self, llm_post=None, use_cache=True):
        self.llm_post = llm_post            # callable(prompt) -> list[dict]（LLMClient._post）
        self.use_cache = use_cache
        self._lock = threading.Lock()
        self._alias = {}                    # name -> (canonical, category)（含缓存与注册表别名）
        self._canon_cat = {}                # canonical -> category
        self._canon_key = {}                # norm_key(canonical) -> canonical
        self._counts = {}                   # canonical -> 使用次数（prompt 头部排序用）
        self._retire = {}                   # 退役 canonical → 替代名（注册表 retired）
        self._expand = {}                   # norm_key → [canonical...]（注册表 expansions）
        self.stats = {"l1": 0, "l2": 0, "l3_llm": 0, "l3_new": 0, "l3_keep": 0}
        self._load_registry()
        if use_cache:
            self._load_cache()

    # ---------- 加载 ----------
    def _load_registry(self):
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            reg = json.load(f)
        self.registry_version = reg.get("registry_version", "")
        # 退役 canonical → 替代名（如 HTML5→HTML：版本号并入母项的口径修正，
        # 同时重定向历史 L3 缓存里指向旧 canonical 的判定）
        self._retire = reg.get("retired", {})
        # 一对多展开（如 "C/C++"→[C, C++]：书写惯例而非独立技术，展开各计一次）
        self._expand = {norm_key(k): v for k, v in reg.get("expansions", {}).items()}
        for canon, d in reg.get("curated", {}).items():
            self._register(canon, d.get("category", ""),
                           aliases=d.get("aliases") or [], keep=True)

    def _remap(self, canonical):
        """退役 canonical → 替代名（链式，带环保护）。"""
        seen = set()
        while canonical in self._retire and canonical not in seen:
            seen.add(canonical)
            canonical = self._retire[canonical]
        return canonical

    def _load_cache(self):
        if not os.path.exists(ALIAS_CACHE_PATH):
            return
        with open(ALIAS_CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                canon = rec.get("canonical")
                if not canon:
                    continue
                self._alias[rec["name"]] = (canon, rec.get("category", ""))
                if canon not in self._canon_cat and rec.get("category"):
                    self._canon_cat[canon] = rec["category"]
                self._canon_key.setdefault(norm_key(canon), canon)
                self._counts[canon] = self._counts.get(canon, 0) + rec.get("n", 1)

    def _register(self, canonical, category, aliases=(), keep=False):
        """登记 canonical 及其别名（keep=True 为注册表层，不计使用次数）。"""
        self._canon_cat.setdefault(canonical, category)
        self._canon_key.setdefault(norm_key(canonical), canonical)
        self._alias.setdefault(canonical, (canonical, category))
        for a in aliases:
            self._alias.setdefault(a, (canonical, category))
            self._canon_key.setdefault(norm_key(a), canonical)
        if not keep:
            self._counts[canonical] = self._counts.get(canonical, 0) + 1

    def _save_cache(self, name, canonical, category):
        os.makedirs(os.path.dirname(ALIAS_CACHE_PATH), exist_ok=True)
        with open(ALIAS_CACHE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "canonical": canonical,
                                "category": category, "n": 1}, ensure_ascii=False) + "\n")

    # ---------- L1/L2 ----------
    def _resolve_known(self, name):
        """L2 精确别名 → L1 norm_key 折叠。命中 → (canonical, category, 'l2'|'l1')。"""
        hit = self._alias.get(name)
        if hit:
            return self._remap(hit[0]), hit[1], "l2"
        k = norm_key(name)
        canon = self._canon_key.get(k)
        if canon:
            canon = self._remap(canon)
            return canon, self._canon_cat.get(canon, ""), "l1"
        return None

    # ---------- L3 ----------
    def _top_canonicals(self):
        return [c for c, _ in sorted(self._counts.items(), key=lambda kv: -kv[1])[:_PROMPT_CANON_CAP]]

    def _llm_resolve(self, names):
        """一批未知名 → {name: (canonical, category)}。merge 目标必须在候选清单内。"""
        canonicals = self._top_canonicals()
        cand = "\n".join(canonicals)
        items = "\n".join(f"{i+1}. {n}" for i, n in enumerate(names))
        rules = CATEGORY_RULES
        prompt = f"""你是技术名词规范化器。给定「已有规范名清单」与「待归一技能点名」，对每个待归一名判断它指向的技术：
- 与清单中某规范名为**同一技术**（仅命名/大小写/分隔符/中英文/简称/俗称差异）→ action=merge，canonical=该规范名
- 清单中没有等价物（独立技术/工具/协议等）→ action=new，canonical=标准写法（保留惯例大小写，如 Docker/MySQL/iOS）
硬规则（违反即错）：
- 只合并同一技术的命名变体；不同代际/组件/相邻技术不得合并（Spring≠Spring Boot≠Spring Cloud、C≠C++≠C#、AngularJS≠Angular、CSS≠CSS3、MyBatis≠MyBatis-Plus）
- merge 的 canonical 必须逐字取自清单；不得发明清单外的合并目标
- category 按以下判定规则选最贴切的一个：
{rules}
严格只输出一个 JSON 数组（以 [ 开头 ] 结尾，即使只有一个结果也必须包成数组），无其他文字：
[{{"name":"...","action":"merge|new","canonical":"...","category":"..."}}

已有规范名清单：
{cand}

待归一（{len(names)} 个）：
{items}"""
        entries = self.llm_post(prompt) or []
        by_name = {}
        cset = set(canonicals)
        for e in entries:
            if not isinstance(e, dict):
                continue
            n, canon = e.get("name"), e.get("canonical")
            cat = e.get("category") if e.get("category") in CATEGORIES else ""
            if not n or not canon:
                continue
            if e.get("action") == "merge" and canon in cset:
                by_name[n] = (canon, cat or self._canon_cat.get(canon, ""))
            else:
                by_name[n] = (canon, cat)          # new（或 merge 目标不在清单 → 按新实体登记）
        return by_name

    def _llm_resolve_safe(self, batch):
        """批级容错：整批失败（解析/网络）对半重试；单名失败保留原名且不缓存（下次重试）。"""
        try:
            return self._llm_resolve(batch)
        except Exception as e:
            if len(batch) > 1:
                out = {}
                mid = len(batch) // 2
                for half in (batch[:mid], batch[mid:]):
                    out.update(self._llm_resolve_safe(half))
                return out
            print(f"[spnorm] 单名归一失败（保留原名不缓存，下次自动重试）{batch}: {e}")
            return {}

    # ---------- 对外 ----------
    def resolve_batch(self, names, progress=None):
        """一批名字 → {name: (canonical, category)}。已知名免费；未知名走 L3（无 llm_post 则保留原名）。"""
        out, unseen = {}, []
        for n in names:
            hit = self._resolve_known(n)
            if hit:
                canon, cat, src = hit
                out[n] = (canon, cat)
                self.stats[src] += 1
                self._counts[canon] = self._counts.get(canon, 0) + 1
            else:
                unseen.append(n)
        for i in range(0, len(unseen), _LLM_BATCH):
            batch = unseen[i:i + _LLM_BATCH]
            if self.llm_post is None:
                for n in batch:                     # 无 LLM（离线/测试）：按新实体保留
                    out[n] = (n, "")
                    self.stats["l3_keep"] += 1
                continue
            with self._lock:
                self.stats["l3_llm"] += 1
            resolved = self._llm_resolve_safe(batch)
            for n in batch:
                if n not in resolved:
                    # 单名失败：本次按原名使用，不登记不缓存（下次运行自动重试）
                    out[n] = (n, "")
                    self.stats["l3_keep"] += 1
                    continue
                canon, cat = self._remap(resolved[n][0]), resolved[n][1]
                out[n] = (canon, cat)
                is_new = norm_key(canon) not in self._canon_key
                self.stats["l3_new" if is_new else "l3_keep"] += 1
                self._register(canon, cat)
                self._alias[n] = (canon, cat)
                if self.use_cache:
                    self._save_cache(n, canon, cat)
            if progress:
                progress(min(i + _LLM_BATCH, len(unseen)), len(unseen))
        return out

    def normalize_skillpoint_map(self, sp_map):
        """{skill: [skillpoint...]} → 归一并去重保序的 canonical 版本。

        先应用注册表 expansions（一对多：书写惯例如 C/C++ → C、C++ 各计一次），
        再对余名做 L1/L2/L3 归一。
        """
        names = set()
        expanded = {}                      # 原名 -> [canonical...]
        for sp in {sp for sps in sp_map.values() for sp in sps}:
            targets = self._expand.get(norm_key(sp))
            if targets:
                expanded[sp] = [self._remap(t) for t in targets]
            else:
                names.add(sp)
        mapping = self.resolve_batch(sorted(names)) if names else {}
        out = {}
        for sk, sps in sp_map.items():
            seen, lst = set(), []
            for sp in sps:
                for c in (expanded.get(sp) or [mapping.get(sp, (sp, ""))[0]]):
                    if c not in seen:
                        seen.add(c)
                        lst.append(c)
            if lst:
                out[sk] = lst
        return out

    def canonical_category(self, canonical):
        return self._canon_cat.get(canonical, "")
