# paper_signal — 论文数据处理层

解析论文 TXT（头块 + 正文）→ **PaperRecord**（arXiv ID / 标题 / 发表日期 / 分档 / 命中维度 /
证据句 / 摘要 / 关键词 / 正文片段），供上层模块消费。本层**不调用 LLM、不产出 ΔG**——论文的
分类（新信号提取 / 提及识别）在 `codes/extractor/`，ΔG 增量层热更新在 `codes/builder/`。

```
论文 TXT ──► paper_parser（精髓提取：头字段 + Abstract/Keywords 双启发式解析）
          ──► paper_source（PaperRecord → 抽样/去重/断点条目，next_batch 返回 PaperRecord）
          ──► 消费方：extractor（分类）/ builder（ΔG 热更新）
```

## 模块文件

| 文件 | 职责 |
|------|------|
| `paper_parser.py` | ★ 解析机制：TXT 头块 + Abstract/Keywords 双启发式提取 → `PaperRecord` |
| `arxiv_ingest.py` | ★ arXiv 全库批次入库：元数据裸格式 TXT → 标准头块格式（S/A 档，索引 xlsx 为权威字段源） |
| `paper_source.py` | 数据源胶水：`(stratum, text)` 条目 → 抽样/去重/断点，`next_batch` 返回 `PaperRecord` |
| `paper_sampler.py` | 通用分层抽样引擎（`StratifiedSampler`，自持拷贝，源无关） |
| `paper_config.py` | 论文路径 + 解析参数（唯一命名，避免跨模块 `import config` 冲突） |

## 命名约定（为什么用唯一命名）

本模块被 extractor / builder 跨模块 `sys.path` 导入。为避免三个模块各自 `config.py`/`sampler.py`
的顶层导入冲突，本层配置与抽样器使用**唯一命名**：`paper_config.py` / `paper_sampler.py`。
内部统一 `import paper_config as config`。

## 数据格式（data/papers/ 下按 `S档_核心/` 等分档目录，每篇 .txt）

```
【arXiv ID】  2504.18651
【标题】      ...
【发表日期】  2025-04-25
【赛题分档】  S 档（得分: 25, 覆盖 2 个维度）
【命中维度】  B_技能图谱与知识图谱、C_数据质量与幻觉防控
【证据句】
  [B_技能图谱与知识图谱] ...原文证据句...
（以下为论文全文正文）
Title / Authors / Abstract ... / Keywords: ... / 1. Introduction ...
```

Abstract 提取双启发式：① 显式标记（`Abstract`/`ABSTRACT`/`摘要`）；② 无标记（arXiv 行向后 /
跳过标题作者机构向前取长段）。Keywords 缺失合法（返回空列表）。解析器纯 stdlib（re），
永不因缺字段失败；乱码字节用 `errors="replace"` 容忍。

## 全库批次入库（arxiv_ingest.py）

全库分批交付的论文（首批 2026-08-27：`arxiv_txt_2022.zip`，arXiv 2022 全量 185,973 篇分档）
为**元数据裸格式** TXT（`Title:`/`Authors:`/`Published:`/`Abstract:`，无全文），与本库
头块格式不同。入库脚本以批次索引 xlsx（`01_全库总索引_*.xlsx`，含分档/总得分/命中维度/
证据句/直链）为权威字段源，把 S/A 档 TXT 转换为标准头块格式写入
`data/papers/<batch>/{S档_核心,A档_重点}/`（原始元数据块原样保留为正文），B/C 档不入库
（与六专题"仅保留 S/A"口径一致）；带跨批次同 ID 防线。转换纯确定性、幂等。

```bash
python codes/paper_signal/arxiv_ingest.py --src <解压后的批次目录> --batch arxiv2022
cd codes/timeline && python run_timeline.py --papers   # 重建映射表
```

2022 批次实测：S 51 + A 2,222 = 2,273 篇并入，映射表 8,097 → 10,370 行，与既有语料
ID 零重叠；2022 年 12 个月全覆盖（112–280 篇/月），基准段 2022-05/06 各 197/167 篇。

## 用法（解析冒烟，不调 LLM）

```bash
cd codes/paper_signal
python -c "from paper_parser import scan_papers; rs=scan_papers(tier='S', limit=3); print(len(rs)); print(rs[0].title[:60])"
```

完整信号提取 / 提及识别 / ΔG 热更新见 `codes/extractor/README.md` 与 `codes/builder/README.md`。
