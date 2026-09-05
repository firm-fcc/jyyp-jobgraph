# 前端5分钟快速接入

## 1. 启动后端

Windows CMD，在交付目录内：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

部署者在本机`.env`填写`LLM_API_KEY`、`LLM_MODEL`、`LLM_API_URL`（完整chat/completions地址）；不要把Key放进Vite环境变量、源码或Git。PowerShell激活命令为`.\.venv\Scripts\Activate.ps1`。

确认`MATCHING_DECISION_THRESHOLD=0.380952`，不要修改。CORS示例已允许`http://localhost:5173`与`http://127.0.0.1:5173`；换域名/端口时修改`CORS_ALLOWED_ORIGINS`并重启后端，禁止`*`。旧`CORS_ORIGINS`只在新变量未设置时作为兼容后备。

```bat
uvicorn app:app --host 0.0.0.0 --port 8000
```

Swagger：`http://127.0.0.1:8000/docs`。

## 2. 配置Vite

前端自己的`.env.local`：

```dotenv
VITE_API_BASE=http://127.0.0.1:8000
```

这是浏览器访问后端的基础地址，不是LLM地址。配置后重启Vite。复制本目录的`frontend_api_types.ts`和`api_client_example.ts`到前端项目；使用现有Vite/TypeScript工具链，无需Axios。

## 3. 正式调用顺序

`getHealth()` → `uploadCandidate(file)` → `getTargetJob(jobId)` → `runMatch(candidate, target)` → `getLearningPath(candidate, match)`。

对应：GET /health → POST /api/candidate → GET /api/target-job/{job_id} → POST /api/match → POST /api/learning-path。

- 健康检查：`llm_configured=false`时不要开启真实模型提取（纯本地`preflight=true`仍可用）；`matching_calibrated=false`时不得展示正式匹配结论。
- 岗位列表：GET /api/jobs，保留`jobid`与`jd_key`；歧义时用`jd_key`精确选择。
- 岗位聚合展示：GET /api/job-summary/{job_code}，不能作为正式Matching输入。匹配只用single-JD TargetJobProfile。
- Match读`match.match_result.match_score`、`.metrics.verified_fit`、`.decision`，不要误读顶层。
- Learning传`match.proficiency.levels`并设`auto_proficiency=false`，不得重复计算。

## 4. 长请求与UI

上传即显示Loading并禁用重复提交：“正在解析简历并评估能力，可能需要数分钟”。当前不是异步任务系统，无task-id/轮询接口。

真实基线Candidate约77秒；Match含proficiency约280秒；完整链路357秒。它们不是SLA。不要设置10/30秒等短超时；反向代理也需允许长等待。不要自动重发POST。用户主动取消可终止UI等待，但不保证后端或供应商调用已取消，不要立即重发。

## 5. 必须保留的语义

- 不重新计算MatchScore、不改threshold、不自己重新定义MATCH/NO_MATCH、不替换为旧cosine匹配。
- 不回退Mock、不写死90.8%等演示准确率；Dev 80%不是正式Matching Accuracy。
- GRAPH_UNAVAILABLE是HTTP200业务结果，显示“当前暂未配置该能力的发展路径”，不报服务器异常、不造步骤。
- Candidate U是证据不足；JD U是等级未指定，均不转P1。学习完成不自动升级等级。

详情：`FRONTEND_API_REFERENCE.md`。Browser React Integration = PENDING，接入者仍需完成真实浏览器联调。
