# 来源真实E2E验收摘要

真实E2E：PASS。此证据从唯一已封存源ZIP继承，**本轮未重新调用模型**。
来源SHA-256：0da466fa745bc54eca6b12a343580a788b1d2206f33fbd4cd2ef74febdf3b615。
机器证据：e2e_runtime_acceptance_final/validation_result.json、stage_timing.json、proficiency_timing.jsonl、learning_path_replay_summary.json。原字节不变。

匿名case_id=candidate_0068；真实PDF输入hash=2b0f0b60e0b317eaff9d9206fe69c8f7c5bde91bcc46274b134f81d7e97f4f65。真实文件与原请求/响应均不在交付包中。
模式：本地ASGI TestClient → 真实服务/DeepSeek，不是Mock，也不是React/远端浏览器验收。

| API | HTTP | 秒 | schema_version |
|---|---:|---:|---|
| Candidate | 200 | 77.162731 | candidate_api_response_v1_1 |
| Target Job | 200 | 0.074199 | target_job_profile_v1.1 |
| Match（含proficiency） | 200 | 280.085036 | matching_pipeline_output_v1 |
| Learning Path | 200 | 0.009853 | learning_path_api_response_v1 |

MatchScore=14.29；Decision=NO_MATCH；threshold=0.380952。
Learning Path：READY=2、GRAPH_UNAVAILABLE=4、NO_ACTION=1。
Candidate skills=2、grounded=3、proficiency=2、required skills=7。

阶段：解析0.075900秒、Evidence22.811180秒、Team Skill53.958951秒、Profile assembly0.000889秒、proficiency280.076119秒、Learning Path0.009853秒。总计357.370663秒（约357.371秒）。
子过程耗时可能嵌套，不应把transport和逻辑阶段重复相加。
7次逻辑LLM调用，9次传输，2次既有内部重试，2次transport错误，0timeout；可核验tokens=26413，不推断无usage失败响应的计费。

真实case的Gap为SATISFIED1、LEVEL_GAP1、MISSING5、EVIDENCE_INSUFFICIENT0；因此不能宣称真实样本覆盖VERIFY_FIRST。该边界由独立离线13项合同测试覆盖。
原Learning Path确定性重放PASS，HTTP200，0模型调用，响应一致；本轮只保存封存摘要。

这是E2E功能验收，**不是正式Matching Accuracy测试**，不使用Gold，不计算Blind指标，不调阈值。
validation内更早的e2e_smoke_v1、e2e_runtime_acceptance_v1与offline合同诊断属于历史，不是本次最终PASS证据。
