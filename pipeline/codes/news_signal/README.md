# news_signal — 新闻数据处理层

解析新闻 TXT（头块 + 正文）→ **NewsRecord**（doc_id / 标题 / 公众号 / 发布时间 / 正文），
供上层模块消费。本层**不调用 LLM、不产出 ΔG**——新闻的分类（过滤/信号提取/提及映射）在
`codes/extractor/`，ΔG 增量层热更新在 `codes/builder/`。

```
新闻 TXT ──► news_parser（头块解析：标题/链接/发布时间/公众号/爬取时间 + 正文）
          ──► news_source（NewsRecord → 抽样/去重/断点条目，next_batch 返回 NewsRecord）
          ──► 消费方：extractor（分类）/ builder（ΔG 热更新）
```

## 模块文件

| 文件 | 职责 |
|------|------|
| `news_parser.py` | ★ 解析机制：TXT 头块 + 正文 → `NewsRecord` |
| `news_source.py` | 数据源胶水：`(stratum, text)` 条目 → 抽样/去重/断点，`next_batch` 返回 `NewsRecord` |
| `news_sampler.py` | 通用分层抽样引擎（`StratifiedSampler`，自持拷贝，源无关） |
| `news_config.py` | 新闻路径 + 解析参数（唯一命名，避免跨模块 `import config` 冲突） |
| `convert_docx.py` | docx 批次转换（一次性，2026-08）：5 源 5,427 篇 .docx → news_raw TXT |
| `import_zip.py` | zip 全量批次入库（一次性，2026-08-30 已执行）：33 源 302,548 篇 byte 保真并入（同名跳过幂等、<200 字与解析器同口径拒绝、超长路径截断）；news_raw 现 33 源 282,945 篇 |

## 数据格式（data/news/news_raw/{公众号}/*.txt）

```
标题: ...
链接: ...
发布时间: 2026-07-27 17:48:07  +0800   （可为空）
作者: ...
公众号: 量子位                        （或"来源: 36氪 RSS"）
爬取时间: 2026-07-28T11:27:07.542725
============================================================
正文……（通常很长，avg 6.7k 字符）
```

- `doc_id` = 相对路径（公众号/文件名），作 ΔG 证据幂等键。
- `pub_date` 优先取"发布时间"，缺省回退"爬取时间"；再缺 → ""（时间衰减用底权）。
- 正文存全文；过短（< `MIN_BODY_CHARS`）跳过。

## 用法（解析冒烟，不调 LLM）

```bash
cd codes/news_signal
python -c "from news_parser import scan_news; rs=scan_news(limit=3); print(len(rs)); print(rs[0].title)"
```

完整新闻 ΔG 热更新见 `codes/builder/README.md`（`run_news_delta.py`）。
