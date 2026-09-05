# JobGraph

面向新一代信息技术领域的岗位能力图谱系统。系统从招聘岗位说明书、行业新闻与学术论文
三类来源持续采集数据，构建岗位—任务—技能—技能点四层图谱，按月推进图谱的时间演化，
并在此基础上提供岗位能力全景、新岗位发现、职业路径探索与人岗匹配诊断四项应用。

线上站点：<https://jyyp-jobgraph.com/>

## 系统构成

系统由三个部分组成，各自独立运行，以数据文件与 HTTP 接口衔接。

| 部分 | 目录 | 形态 | 职责 |
|---|---|---|---|
| 图谱构建管线 | `pipeline/` | Python 离线批处理 | 多源数据清洗、抽取、聚合与演化计算，按时间窗口产出图谱快照 |
| 可视化前端 | `frontend/` | React 静态站点 | 图谱、演化与匹配结果的呈现，五个页面 |
| 人岗匹配服务 | `backend/` | FastAPI 在线服务 | 简历解析、能力比对、差距分析与学习路径规划 |

数据自左向右流动：

```
招聘 JD ┐
行业新闻 ├─► pipeline/ ──► 图谱快照（按月窗口）
学术论文 ┘                      │
                                │ frontend/data-pipeline/ 转换
                                ▼
                        frontend/public/data/ ──► frontend/ 静态站点
                                                        │
                            简历上传 ──► backend/ ───────┘
```

图谱链路为离线管线，站点直读其静态产物，无须在线服务；人岗匹配为唯一需要在线服务的
功能，由 `backend/` 承担。

## 目录结构

```
.
├── pipeline/              图谱构建管线
│   ├── codes/             九个处理模块的源码
│   ├── classify/          岗位、任务、技能三套分类体系与评测集
│   ├── docs/              算法设计、代码说明、数据说明
│   ├── introduction/      体系与方案的说明文档
│   ├── test-suite/        JD 解析准确率测试方案（121 条 JD 与标注真值）
│   └── unit-tests/        单元测试（36 个文件、178 个用例）
├── frontend/              可视化前端
│   ├── src/               页面、图元、数据层与工具
│   ├── public/data/       前端直读的图谱产物
│   ├── data-pipeline/     图谱快照到前端格式的转换脚本
│   └── tests/             单元测试（16 个文件、98 个用例）
├── backend/               人岗匹配服务
│   ├── app.py             HTTP 入口
│   ├── backend/           服务层
│   ├── candidate_core/    简历解析、匹配、差距分析与路径规划的核心实现
│   ├── tests/             单元测试
│   └── validation/        端到端验收记录
├── deploy/                部署脚本与服务配置
└── package.json           构建入口，转发至 frontend/
```

## 快速开始

### 可视化前端

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5176
npm run build        # 类型检查后构建静态产物
```

在仓库根执行 `npm run dev`、`npm run build`、`npm run typecheck` 与上述命令等价，
根 `package.json` 仅作转发，差别在于根构建把产物写入根 `dist/`。

前端所需的图谱产物已随仓库提供（`frontend/public/data/`），无须先运行管线。
Node 版本须为 20 及以上。

### 人岗匹配服务

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Linux/macOS 为 .venv/bin/pip
cp .env.example .env                               # 填写 LLM_API_KEY、LLM_MODEL、LLM_API_URL
.venv/Scripts/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

服务启动后，接口文档位于 `http://127.0.0.1:8000/docs`。前端一侧在 `frontend/.env.local`
写入 `VITE_MATCH_API=http://127.0.0.1:8000` 即接通实测链路；未配置该项时人岗匹配页
回落至演示链路，其余各页不受影响。

### 图谱构建管线

```bash
cd pipeline
pip install -r requirements.txt
echo "<模型 API key>" > codes/api-key.txt
python codes/graph/run_pipeline.py --window 2026-04
```

管线的全部参数集中于 `codes/settings.yaml`。原始数据集（招聘 JD、新闻、论文）体量以
GB 计，不随仓库分发，路径由该文件配置；仓库内提供的是完整源码、分类体系与评测集。
各模块的运行方式见 `pipeline/README.md` 与 `pipeline/docs/code-description.md`。

## 数据与口径

### 分类体系

| 体系 | 文件 | 规模 |
|---|---|---|
| 岗位 | `pipeline/classify/Jobs/jobs_v2.json` | 9 个类别、145 个岗位，含定义、判定关键词与边界说明 |
| 任务 | `pipeline/classify/Tasks/tasks.json` | 64 项，扁平无层级 |
| 技能 | `pipeline/classify/Skills/skills0821.json` | 54 项，按两个维度、十个组归属 |

技能点为开放集合，由管线从 JD 正文中抽取并归一，不预先定义。

### 图谱产物

末窗（2026-04）的图谱规模：

| 层 | 节点数 | 边 | 数量 |
|---|---:|---|---:|
| 岗位 | 105 | 岗位—任务 | 3,571 |
| 任务 | 98 | 岗位—技能 | 4,618 |
| 技能 | 65 | 任务—技能 | 2,945 |
| 技能点 | 20,192 | 技能—技能点 | 30,962 |

时间维覆盖 2022-05 至 2026-04 共 46 个月度窗口。基础数据为 578.9 万条原始招聘记录，
其中 49.7 万条命中岗位体系，经去重与分层降采样后 37.8 万条进入抽取链路。岗位体系中
登记的招聘信息条数合计 510.4 万条，是岗位层唯一的实测计量。

跨窗口的绝对量不可比：早期窗口的原始记录数远低于近期窗口，涉及规模的比较一律使用
同层份额而非绝对强度。

### 数据来源的分工

| 来源 | 在图谱中的角色 | 信号特征 |
|---|---|---|
| 招聘 JD | 基图，反映市场当前需求 | 相对技术出现滞后 3 至 12 个月 |
| 行业新闻 | 增量层辅助信号 | 领先招聘需求 6 至 18 个月 |
| 学术论文 | 增量层主信号 | 领先招聘需求 1 至 3 年 |

三源经交叉验证后合成有效图谱，合成层不改写基图数值，两层各自独立保存。

## 页面

| 路由 | 页面 | 内容 |
|---|---|---|
| `/landing` | 封面 | 系统定位与规模指标 |
| `/home` | 首页 | 本期结论、榜单与四层体系入口 |
| `/panorama` | 全景图谱 | 领域内岗位的能力要求分布与前瞻信号 |
| `/jobs` | 岗位洞察 | 新岗位发现与既有岗位的能力要求变化 |
| `/explore` | 职业探索 | 由能力出发的岗位可达性分析 |
| `/match` | 人岗匹配 | 简历与目标岗位的差距分析与学习路径 |

路由采用 `HashRouter`，全部路由挂在 `#` 之后，服务端只需交付 `index.html` 一个入口。
图表均为手绘 SVG，未引入图表库。

## 测试

三套测试相互独立，各自在所属目录内运行。

| 范围 | 位置 | 规模 | 语句覆盖率 |
|---|---|---|---:|
| 图谱构建管线 | `pipeline/unit-tests/` | 36 个文件、178 个用例 | 62.6% |
| 可视化前端 | `frontend/tests/` | 16 个文件、98 个用例 | 90.5% |
| 人岗匹配服务 | `backend/tests/` | 306 个用例及 174 个子用例 | 82.0% |

```bash
python pipeline/unit-tests/run_tests.py            # 管线：覆盖率与逐用例报告
cd frontend/tests && npm install && npm run test:coverage
cd backend && python -m pytest -q --cov           # 需先安装 requirements-test.txt
```

各套件的覆盖率口径、排除项与设计原则见所在目录的 README。管线的 178 个用例中有 3 例
针对真实模型端点，无密钥的环境自动跳过，此时离线的 175 例给出 61.3% 的覆盖率，同样
高于 60% 的门槛。

除单元测试外，`pipeline/test-suite/` 提供 JD 解析准确率的集成测试方案，含 121 条真实
招聘记录与逐条标注的真值，实测三维度归类准确率 92.5%；`backend/validation/` 保存人岗
匹配链路的端到端验收记录。

## 部署

线上采用单主机同域交付：Nginx 分流，静态产物由文件系统直接交付，`/api/` 与 `/health`
反向代理至本机的匹配服务。完整步骤、主机选型依据、验收命令与已知约束见
[`deploy/README.md`](deploy/README.md)，脚本位于 `deploy/scripts/`。

前端产物亦可发布至任意静态托管服务。因路由不依赖服务端重写规则，托管侧无须额外配置。

## 技术栈

| 部分 | 主要依赖 |
|---|---|
| 前端 | React 18、TypeScript 5.6、Vite 5.4、React Router 6 |
| 匹配服务 | Python 3.11+、FastAPI、Pydantic、PyMuPDF、python-docx |
| 构建管线 | Python 3.11+、PyYAML，模型调用采用 OpenAI 兼容接口 |
| 测试 | Vitest、Testing Library、pytest、pytest-cov |

## 许可

保留全部权利，未附加开源许可。仓库内的第三方数据与文献资料版权归各自所有者。
