# -*- coding: utf-8 -*-
"""JD 侧提示词模板（信号提取 + 确证提及）。

设计原则（与论文/新闻提示词一致，历史反馈沉淀）：
- **不硬编码数量配额**："0 条或任意条，宁缺毋滥"
- **仅显式信号**：JD 必须直接写明才记录，禁止推断
- **新信号必带定义与证据**：name_zh/name_en + definition（它是什么）+ evidence（逐字原文）
- evidence 必须来自提供的原文，禁止编造

JD 侧语义（与论文/新闻不同，务必区分）：
- 招聘 JD 是市场当前需求的**确证源**（不是前瞻源）：JD 中出现的技能/任务即"市场已在招"
- **不从 JD 发现新岗位**（岗位体系沿用 51job funtype 分类）；new_job 候选仅当 JD 明确出现
  新兴职业角色称谓时输出——用于**确证叠层已跟踪的前瞻岗位**（见 overlay_labels 清单），
  不会从零创建新岗位条目（流水线对全新 new_job 丢弃）
- 叠层已跟踪的前瞻实体（overlay_labels）在 JD 中被明确提到时，输出为 mention（确证），
  而不是 new_signal（避免重复建条）

本文件为**唯一命名**模块，被 jd_extractor 使用；builder 经 sys.path 跨模块导入。
"""

# ---------------- 信号提取（Stage A） ----------------
JD_EXTRACT_PROMPT = """你是招聘市场信号分析师，服务于"岗位能力图谱"系统的叠层确证与市场信号提取。招聘 JD 代表市场**当前真实需求**——JD 中出现的要求即"市场已在招"。你的任务是从 JD 标题与职位描述中提取**显式信号**。

输入：若干条 JD 的 JSON 数组。每条包含 jd_index / title（职位名）/ funtype（平台职能分类）/ pub_date / body（职位描述截断片段）。

叠层已跟踪的前瞻实体（图谱正在观察、等待市场确证的新技能/任务/岗位；JD 明确提到它们时**输出为 mention 而非 new_signal**）：
{overlay_labels}

请为每条 JD 输出两部分：

一、new_signals：JD 明确要求的、**超出常规体系**的技能/任务/技能点（市场已出现但体系尚未收录的新信号）。每条：
- kind: new_skill（稳定能力类别）| new_task（抽象工作类别）| skillpoint（具体工具/框架/语言）| new_job（仅限新兴职业角色称谓，用于确证叠层跟踪的岗位）
- name_zh: 中文名（**简洁自足**——任务/技能通常 4-12 个汉字、上限 14，岗位 4-10 个汉字；优先「动作+对象」或「限定+核心」结构，避免堆砌"的/与/及"、避免重复词）
- name_en: 英文原名（JD 中出现则保留；无则空字符串）
- definition: 定义——"它是什么"，1-2 句，让读者脱离 JD 也能理解
- evidence: 原文句子数组（必须逐字引用职位描述，禁止编造）
- confidence: high | medium | low

二、mentions：JD 中提及的既有技能/任务/岗位名称（含上表叠层跟踪实体）。每条：
- type: skill | task | job
- name: 提及的名称
- evidence: 原文句子数组（必须逐字引用）

严格要求：
- **只提取显式信号**：JD 必须直接写明才记录；禁止从上下文推断、禁止脑补。
- 常规通用要求（如"熟练办公软件""良好沟通能力"）不输出；聚焦 IT 专业技能/任务。
- 每条 new_signals 必须同时含 definition 与 evidence；缺一不可，否则不要输出该条。
- 宁缺毋滥：无新信号则 new_signals 为空数组；无提及则 mentions 为空数组。
- 不硬编码数量。

输出 JSON 对象，格式：
{{
  "jd_signals": [
    {{
      "jd_index": 0,
      "new_signals": [
        {{"kind": "new_task", "name_zh": "...", "name_en": "...", "definition": "...",
         "evidence": ["原文句..."], "confidence": "high"}}
      ],
      "mentions": [
        {{"type": "skill", "name": "...", "evidence": ["原文句..."]}}
      ]
    }}
  ]
}}
仅输出该 JSON 对象。"""
