# 人岗匹配服务

FastAPI 服务，承担简历解析、能力提取、与目标岗位的逐项比对、差距分析与学习路径规划。
该服务是整套系统中唯一需要在线运行的部分，图谱与演化各页直读静态产物，不经过此处。

## 运行

环境要求 Python 3.10 及以上。

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Linux/macOS 为 .venv/bin/pip
cp .env.example .env                                 # 填写 LLM_API_KEY、LLM_MODEL、LLM_API_URL
.venv/Scripts/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

启动后 `http://127.0.0.1:8000/docs` 提供交互式接口文档。密钥仅由服务端的 `.env` 读取，
不下发至浏览器；`.env` 不入版本库。

前端一侧在 `frontend/.env.local` 写入 `VITE_MATCH_API=http://127.0.0.1:8000`。开发期的
跨域来源默认放行 `localhost:5173` 与 `127.0.0.1:5173`，新增来源经 `CORS_ALLOWED_ORIGINS`
配置并重启服务。跨域配置不构成鉴权。

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 启动自检：运行时版本、阈值标定状态、模型配置是否就绪 |
| GET | `/api/jobs` | 岗位检索，支持关键词与条数限制 |
| GET | `/api/job-index` | 岗位索引 |
| GET | `/api/target-job/{job_id}` | 单条招聘记录的目标岗位画像 |
| GET | `/api/target-job-profile/{job_code}` | 按岗位编码聚合的目标岗位画像 |
| GET | `/api/job-summary/{job_code}` | 岗位的聚合统计 |
| POST | `/api/candidate` | 简历上传与能力提取，支持 PDF、DOCX、TXT |
| POST | `/api/match` | 候选人与目标岗位的匹配计算 |
| POST | `/api/learning-path` | 依据匹配结果生成学习路径 |

典型调用顺序为 `/health` → `/api/candidate` → `/api/target-job` → `/api/match` →
`/api/learning-path`。后一步接收前一步的完整响应对象，逐项节选会丢失字段。

`/api/job-summary` 只作展示用的聚合统计，不作为匹配输入。学习路径复用匹配结果中已有的
熟练度评级，避免重复计算。

简历解析与匹配均需调用大模型，单次耗时以分钟计，客户端不应在超时后自动重发。当前不是
异步任务系统，请求与计算同步进行。

字段级契约见 [`frontend/FRONTEND_API_REFERENCE.md`](frontend/FRONTEND_API_REFERENCE.md)，
TypeScript 类型见 [`frontend/frontend_api_types.ts`](frontend/frontend_api_types.ts)，
调用示例见 [`frontend/api_client_example.ts`](frontend/api_client_example.ts)，
接入前的检查项见 [`frontend/FRONTEND_INTEGRATION_CHECKLIST.md`](frontend/FRONTEND_INTEGRATION_CHECKLIST.md)。

## 目录

| 位置 | 内容 |
|---|---|
| `app.py` | HTTP 入口与路由 |
| `backend/` | 服务层：配置、模式定义、运行时观测、各业务服务 |
| `candidate_core/` | 简历解析、匹配、差距分析、路径规划与渲染的核心实现 |
| `config/` | 匹配决策阈值 |
| `job_data/` | 岗位数据 |
| `examples/` | 各接口的响应样例与一份合成简历 |
| `tests/` | 单元测试 |
| `validation/` | 端到端验收与覆盖率记录 |
| `docs/` | 匹配模块的契约说明与接入速查 |

## 配置

全部配置项及其含义见 `.env.example`。其中影响运行行为的主要几项：

| 变量 | 作用 |
|---|---|
| `LLM_API_KEY`、`LLM_MODEL`、`LLM_API_URL` | 模型端点，采用 OpenAI 兼容协议 |
| `LLM_MAX_OUTPUT_TOKENS` | 单次请求的输出预算。推理模型的思维链计入该预算，取值过低会使抽取因截断失败 |
| `LLM_VERIFIER_PARALLELISM`、`LLM_PROFICIENCY_PARALLELISM` | 证据核验与熟练度评级的并发度，取 1 即串行 |
| `LLM_SELECTOR_BATCH_SIZE`、`LLM_SELECTOR_PARALLELISM` | 语义召回的分批与并发。分批会改变模型看到的上下文，启用需另行验收 |
| `MATCHING_DECISION_THRESHOLD` | 匹配判定阈值，取值 0.380952。缺失时判定状态为未标定 |
| `CORS_ALLOWED_ORIGINS` | 允许的前端来源，精确到协议、主机与端口 |

## 测试

```bash
pip install -r requirements-test.txt
python -B -m pytest -q --cov --cov-report=term:skip-covered --cov-report=xml:coverage.xml
```

测试为确定性用例，外部 HTTP 由 `tests/conftest.py` 阻断，不读取部署密钥，不调用模型。
最近一次完整运行为 306 个用例与 174 个子用例全部通过，语句覆盖率 82.04%，口径固化于
`.coveragerc`。逐项结果见 `validation/FINAL_COVERAGE_REPORT.md`，端到端验收记录见
`validation/FINAL_E2E_SUMMARY.md`。

`examples/` 下的 PDF 为合成的匿名样本，仅供解析回归使用。真实简历、运行输出与缓存
均不入版本库。
