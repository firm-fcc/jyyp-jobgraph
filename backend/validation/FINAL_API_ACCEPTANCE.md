# Final API Acceptance · 2026-09-03

来源包的历史真实E2E验收记录；本轮未重跑。当前前端接入检查见FINAL_BACKEND_ACCEPTANCE.md，原JSON证据位于本目录e2e_runtime_acceptance_final/。

总体状态：**PASS（本地真实Backend Runtime Smoke）**。输入来自真实匿名PDF，真实模型生成Candidate与proficiency，未mock模型；部署/浏览器联调未执行。

| Endpoint | HTTP | schema_version | 实测 |
|---|---:|---|---|
| GET /health | 200 | HealthResponse，无schema_version字段 | 配置readiness |
| POST /api/candidate | 200 | candidate_api_response_v1_1 | 77.162731秒；Skill2、grounded capability3，原文offset通过 |
| GET /api/target-job/133663124 | 200 | target_job_profile_v1.1 | 0.074199秒；2022-10真实JD，7项要求 |
| POST /api/match | 200 | matching_pipeline_output_v1 / match_result_v1 | 280.085036秒，含真实proficiency2项；14.29 / NO_MATCH |
| POST /api/learning-path | 200 | learning_path_api_response_v1 | 0.009853秒，复用本次levels，auto_proficiency=false |
| GET /api/jobs | 200 | job_catalog_response_v1 | 既有确定性真实结构数据验收及本轮回归 |
| GET /api/job-summary/AID-01 | 200 | aggregated_job_summary_v1 | 既有确定性统计验收及本轮回归，不作为Matching输入 |

Gap：SATISFIED1、LEVEL_GAP1、EVIDENCE_INSUFFICIENT0、MISSING5。
Learning Path：READY2、GRAPH_UNAVAILABLE4、NO_ACTION1。
无图谱不伪造步骤，不改成SATISFIED/NONE。本次真实样本未出现VERIFY_FIRST；该模式由明确离线专项覆盖，不冒充真实分支覆盖。

真实E2E357.370663秒，7逻辑调用、9传输、2次原内部重试、0timeout。只进行一次本轮获准运行，无人工语义重跑。threshold仍为verified_fit的0.380952；14.29是单候选匹配分数，不是准确率。

完整响应、请求及planner/renderer输入在本地私有重放目录保存，不进入ZIP。随后阻断网络/LLM后Learning Path重放HTTP200，响应与原真实调用逐字段一致。公开摘要见validation/e2e_runtime_acceptance_final/learning_path_replay_summary.json。

## 历史与边界

validation/e2e_smoke_v1/是最初900秒超时历史；validation/e2e_runtime_acceptance_v1/是旧Learning400摘要，均非当前结果。旧400原响应未落盘，不能还原精确栈。
validation/learning_path_contract_diagnosis.json为独立离线复现/修复对照，不是真实模型结果。

Malformed request仍4xx；未知skill由冻结registry既有校验拒绝。缺阈值仍NOT_CALIBRATED，非法阈值仍拒绝；本次合法阈值下NO_MATCH。Candidate、Matching、Proficiency和Learning Path冻结实现不变。

本次ASGI TestClient调用真实后端/子进程/外部LLM，不等同真实TCP部署或浏览器验收。未读取Gold或计算正式系统准确率。
