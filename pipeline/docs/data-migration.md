# 数据迁移包说明（export_graph_bundle.py → graph_data_bundle_*.zip）

> **用途**：把图谱更新管线运行所必需、且**不会经 GitHub 同步**（gitignored）的数据打包成单个 zip，
> 用于把项目迁移到其它设备后**无需重跑历史窗口**即可继续逐窗更新图谱。
> 脚本：仓库根 [`export_graph_bundle.py`](../export_graph_bundle.py)；产物名 `graph_data_bundle_YYYYMMDD_HHMM.zip`（已加入 .gitignore，不会被提交）。

## 一、快速三步

```bash
# ① 源机（仓库根运行）
python export_graph_bundle.py                # 先 --dry-run 可预览体量
# ② 拷贝 zip + 同名 .sha256 到目标机（U 盘 / 内网传输；勿公网——含未公开 JD/论文全文）
# ③ 目标机
git clone git@github.com:Pyecv/Challenge26.git && cd Challenge26   # 或既有仓库 git pull 到最新
unzip graph_data_bundle_*.zip -d .           # 解压到【仓库根】覆盖（包内即仓库相对路径）
#    Windows 资源管理器解压时注意选择"解压到 Challenge26/"且不要多套一层目录
# ④ 手动放置密钥（包内默认不含）：codes/api-key.txt（DeepSeek key，一行纯文本）
# ⑤ 验证（见第四节）
```

## 二、包内路径 ↔ 恢复位置

**包内所有文件按仓库相对路径存放**，在仓库根解压即自动落位，无需手工搬运。关键子树：

| 包内路径（= 恢复位置） | 内容 | 作用 / 主要消费方 |
|---|---|---|
| `data/timeline/jd/*.csv` | JD 月度时间线 53 个文件（2021-06..2026-05，~6.3GB） | 图谱逐窗管线 Stage A/S 的输入（`graph/run_pipeline.py --window`） |
| `data/timeline/jd_derived/` | 逐窗 Stage A/B/C 产物（分类/抽取/熟练度结果） | Stage D 消费；LLM 产物重建昂贵，迁移即省全量重跑 |
| `data/timeline/news/`、`news_derived/` | 新闻映射表 282,944 行 + 月度抽样记录（cap=800） | `run_news_delta.py --window` |
| `data/timeline/papers/` | 论文映射表 | `run_paper_delta.py --window` |
| `data/news/news_raw/` 等 | 新闻语料 33 源 282,945 篇 + 统计/拒绝清单 | 新闻 ΔG 与 timeline 重建 |
| `data/papers/` | 论文语料（六专题全文 + arxiv2022 + 总索引 xlsx，~2.5GB） | 论文 ΔG |
| `data/graph/{窗口}/` | 图谱快照三层（base/delta/effective）+ 跨窗 freq 历史 | **续跑必需**：下一窗基图聚合读上一窗 `freq.json` 等 |
| `codes/extractor/cache/` | LLM 文本指纹缓存（句级/证据/熟练度，~259MB） | 断点续跑、同文本零重复调用 |
| `codes/extractor/output/`、`codes/jd_annotate/output/`、`codes/graph/output/` | 各阶段输出与缓存（如 `jd_stack_cache.jsonl`） | 增量续跑与审计 |
| `classify/DeltaG/` | ΔG 增量层的**断点/checkpoint 与日志**（增量 json 本体已入 git，由 clone 提供） | 新闻/论文/JD ΔG 断点衔接 |
| `classify/backup/` | 转正写入前的自动备份 | 回滚用 |
| `classify/Tasks|Skills/` 的 `builder_log`/`checkpoint` | 体系构建期断点（历史，体量极小） | 审计 |
| `data/examples/`、`data/it_jobs_summary.json` | 样例与汇总 | 测试/参考 |

包内还含 `MANIFEST.json`（生成时间、git HEAD、各子树文件数/字节数、所用开关），恢复后可据此核对。

## 三、不在包内的东西（需另行处理）

| 项 | 原因与处理 |
|---|---|
| `codes/api-key.txt` | 密钥绝不默认入包。目标机手动创建（一行 DeepSeek key）。确需随包走：`--include-api-key`，**传输必须走可信通道**，到位后建议换 key |
| `data/jd_dataset/`（62 CSV，~6.4GB） | 与 `data/timeline/jd/` 内容重复（同一批行的两种组织）。图谱运行只需后者；若目标机要**重建时间线或溯源原始 CSV**，导出时加 `--include-jd-dataset` |
| 本地 MySQL `51job` 库（4,861 万行） | 数据库无法入 zip。已有月度 CSV 覆盖全部历史窗口，**继续逐窗更新不需要它**；仅抓取**新**窗口 JD 时需按 `codes/jd_fetch/` 重建（注意：`codes/jd_fetch/config.yaml` 含明文库凭证且已随仓库同步，目标机改为自己的库） |
| 仓库外源压缩包（文献图书馆 / 新闻内容爬取结果.zip 等） | 在仓库目录之外，按需单独拷贝 |
| Python 环境 | 仓库未含 requirements：Python 3.13 + `PyYAML`（+ 开发用 `pytest`；`python-docx` 仅 docx 转换需要） |
| 代码 / 文档 / 体系 JSON（Tasks/Skills/Jobs/taxonomy_base） | 已在 git 中，`git clone` / `pull` 获取 |

固定排除（任何情况下不入包）：`__pycache__`、`.pytest_cache`、`*.pyc`、`_explore/` 探索产物、本地 Agent 指引（CLAUDE.md 等）、**自身产物**（`graph_data_bundle_*.zip/.sha256`——包文件因 .gitignore 属"忽略集"，不排除会把旧包吞进新包）。

## 四、恢复后验证清单

1. **完整性**：`sha256sum -c graph_data_bundle_*.sha256`（Windows：`certutil -hashfile <zip> SHA256` 对比）；解压无报错；
   抽查 `MANIFEST.json` 的 `total_files` 与解压文件数（差 1 = MANIFEST 自身）。
2. **基线一致**：`git log -1` 的 HEAD ≥ `MANIFEST.json` 的 `git_head`（若仓库更新，`classify/` 下体系文件以仓库为准）。
3. **单测**：仓库根 `python -m pytest`（67 项 fixtures 全绿）。
4. **冒烟（不调 LLM）**：
   ```bash
   cd codes/builder && python run_news_delta.py --dry-run     # 能解析出新闻语料
   cd ../graph && python run_pipeline.py --help               # 参数就绪
   ```

## 五、注意事项

- **安全**：包含未公开数据（JD 原文、论文全文），不要上传公网网盘 / 公开仓库；若用了 `--include-api-key`，传完即改密钥。
- **时点**：包生成于特定 git HEAD 与工作区状态。若导出时存在未提交改动，`classify/DeltaG` 未提交增量会兜底入包（MANIFEST 有记录），恢复后如与仓库内容冲突，diff 确认后以较新者为准。
- **体积**：默认包原始 ~11GB（压缩后预计 3-4GB）；`--level 1` 更快但包更大，`--store` 最快（~11GB）。
- **幂等**：脚本可重复运行；输出文件名带时间戳，不会互相覆盖。
