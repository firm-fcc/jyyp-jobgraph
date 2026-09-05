# 浏览器接入前检查

状态取PASS/PARTIAL/FAIL。此表为后端就绪检查，不是React验收证明。

| 项目 | 状态 | 证据 / 边界 |
|---|---|---|
| /health HTTP200 | PASS | test_api + test_frontend_integration；无Key仍可读health，llm_configured=false |
| CORS合法双origin OPTIONS | PASS | localhost:5173 /127.0.0.1:5173，GET/POST预检 |
| CORS合法GET/POST | PASS | 实际health及candidate preflight响应Header |
| CORS非法origin | PASS | 不获allow-origin；凭据模式拒绝通配*；不是身份鉴权 |
| 7类API存在且可访问 | PASS | OpenAPI全7路由 + 本地离线API回归；本轮Candidate仅preflight/合同测试，真实生成依赖封存E2E |
| OpenAPI | PASS | /openapi.json正常；请求Schema正常；字典响应以serializer和TS补充 |
| TypeScript strict | PASS | tsc5.9.3 --noEmit --strict -p frontend/tsconfig.json，无any |
| threshold | PASS | 模板0.380952；匹配读后端decision，前端不重算 |
| GRAPH_UNAVAILABLE | PASS | 原13项service合同回归保留，包括VERIFY_FIRST/MISSING/LEVEL_GAP，HTTP200空步骤 |
| 有图谱 / SATISFIED /非法请求 | PASS | 原路径正常、无需学习NO_ACTION、非法请求仍4xx |
| .env.example | PASS | KEY/MODEL/URL为空，CORS配置示例；本包不含.env |
| README启动方式 | PASS | 当前Python/uvicorn入口可导入；完整测试同一交付目录执行 |
| pytest / coverage | PASS | 306+174subtests、0失败/跳过；82.04% >=60% |
| 来源真实E2E | PASS | 4业务接口200，357.371秒，路径READY2/GRAPH_UNAVAILABLE4/NO_ACTION1；原证据保留 |
| 实际React浏览器操作 | PARTIAL | 未运行React、未测前端Loading/取消/代理超时；不冒称PASS |
| 实际部署机/远端网络 | PARTIAL | 由接入方配置LLM/TLS/访问控制/资源限制与新Origin |

**Browser React Integration = PENDING**

本轮API called=no；没有重算匹配准确率，没有调参或修改冻结核心。
上线前人工确认：VITE_API_BASE可达；Loading与禁重复提交；不自动重发长POST；Learning复用Match等级；三类路径状态可显示；错误不回退Mock；私有简历与Key不进日志/仓库。
