# E2E Runtime Diagnosis — 2026-09-03

来源包的历史诊断记录；本轮只归档，不重新运行LLM。本文件下文validation/前缀均相对于交付根目录。

Status: PASS — 最终真实Backend Runtime E2E通过；历史失败保留于下文。

## 最终 service/API 合同修复与验证

历史400的原始payload未保存，不能从计数重建。用户明确批准改用独立离线合同用例修复，然后执行一次真实E2E。

离线探针HTTP400正文为 `VERIFY_FIRST must contain exactly one planner verification task`。Planner输出EVIDENCE_INSUFFICIENT / VERIFY_FIRST / GRAPH_UNAVAILABLE / 空步骤，Renderer的单步骤校验抛ValueError。完整离线输入、request schema、planner/renderer输入及trace见 `validation/learning_path_contract_diagnosis.json`，明确不是旧真实请求重放。

只修改API/service适配层：Planner明确GRAPH_UNAVAILABLE时不进入要求非空步骤的Renderer，返回现有RenderedSkillPath结构，保留gap与mode，不虚构步骤/验证任务，不改成SATISFIED或NONE。正常路径仍使用Frozen Renderer，其他非法合同继续拒绝。非法skill ID复用原registry校验。Frozen Core修改0。

离线专项13项通过；真实最终运行后使用其保存的真实请求再次无模型重放，HTTP200、响应完全一致、Planner输入/输出不变。

### 最终单次真实E2E

- 输入candidate_0068，1页真实匿名PDF；2处联系方式、3个论文题名脱敏，其余原文不变。
- Input SHA-256：`2b0f0b60e0b317eaff9d9206fe69c8f7c5bde91bcc46274b134f81d7e97f4f65`。
- document parse：0.075900秒；无独立segmentation（冻结普通PDF full-resume分支）。
- evidence extraction：22.811180秒；Team Skill：53.958951秒；assembly：0.000889秒；subprocess：77.142859秒。
- Candidate HTTP200：77.162731秒；Target HTTP200：0.074199秒。
- Proficiency：280.076119秒；Match HTTP200：280.085036秒（包含前者）。
- Learning Path HTTP200：0.009853秒；总耗时：357.370663秒。
- 7 logical API calls / 9 transport attempts / 2既有内部retry / 2 AgenticLLMResponseError / 0timeout；可核验tokens26,413。最长85.377193秒、平均已完成transport39.310873秒。失败响应无usage，不推测其计费。
- Candidate Skill2、grounded capability3、proficiency2、JD要求7；MatchScore14.29、NO_MATCH、threshold0.380952未改。
- Gap：SATISFIED1、LEVEL_GAP1、EVIDENCE_INSUFFICIENT0、MISSING5。
- Learning：READY2、GRAPH_UNAVAILABLE4、NO_ACTION1；本次真实样本没有VERIFY_FIRST，不将离线覆盖冒充真实分支覆盖。
- 最终pytest296 passed、0failed、0skipped，另174subtests；coverage4333/5283=82.02%，scope未变。
- 测试PDF和预览清理，私有重放数据保留在交付目录外，不进入ZIP。公开摘要见 `validation/e2e_runtime_acceptance_final/`。

## 以下为历史239.65秒运行及诊断（非当前最终结果）

## 输入与权限

用户明确授权将 candidate_0068 去标识化文本发送至 https://api.deepseek.com/chat/completions，使用 deepseek-v4-flash，承担本次 smoke 费用。第一次启动曾在创建进程前被审批拒绝；用户补充明确接收端授权后，实际仅执行一次模型链。

源为真实 Pilot 匿名简历，不读取 Gold/人工标注答案。去除2行残余联系方式、替换3个可检索论文题名；项目、职责、技术行为、方法、结果文字不改。生成1页PDF，解析文字除排版空白外逐字校验通过，视觉检查通过。测试PDF不进入公开交付ZIP；结束后只保留匿名ID、hash、计时和接口验收摘要，临时PDF与预览删除。

- candidate_id: candidate_0068
- PDF SHA-256: ee799234c9eca3cfee047df2e1f418ef1581ba5edff73e3e7446360349e9b553
- 真实JD: 133663124 / 2022-10
- threshold: 0.380952（不重新标定）
- HTTP模式: 本地ASGI TestClient，真实服务与真实LLM，非Mock，非浏览器部署联调

## 900秒定位

上一轮输入实际为591字符的synthetic PDF，非超长简历。900秒发生于 Candidate service 对子进程整体的 asyncio.wait_for，而非单个API请求时限。冻结CLI单次client timeout仍90秒，技术重试最多2次；多逻辑调用/合同重试可能累计超过单次期限。

原请求在父service和冻结CLI各parse一次。现已复用子进程的canonical source，通过短生命周期sidecar传回并删除，不再重复parse。原Python3.10 asyncio.TimeoutError兼容捕获沿用；timeout仍900秒，未提高。

旧运行没有阶段计时，无法倒推出当时哪个模型调用卡住。不能声称本轮消除重复parse就证明修复了旧网络慢请求根因。本次parse仅0.084722秒，显然不是主要耗时；真实LLM耗时占主导，本次最长算法阶段是Team Skill。

Candidate不调用proficiency；它在Match中下游按需计算。本次向Learning Path传入同一真实等级并令auto_proficiency=false，无重复熟练度计算。

## 本次真实阶段耗时

| 阶段 | 秒 | 说明 |
|---|---:|---|
| document parse | 0.084722 | 单次 |
| segmentation | 不执行独立分段 | frozen普通PDF full-resume分支；没有人为跳过阶段 |
| evidence extraction | 43.885675 | 真实模型 |
| Team Skill pipeline | 124.934403 | 包含selector/verifier和1次既有技术重试 |
| candidate profile assembly | 0.000963 | 原函数合计 |
| Candidate subprocess | 169.198355 | 包含以上阶段，不重复相加 |
| Candidate HTTP | 169.227451 | 200 |
| TargetJobProfile HTTP | 0.078419 | 200 |
| proficiency | 70.297518 | 真实计算2项 |
| Match HTTP | 70.306365 | 200，包含proficiency |
| Learning Path HTTP | 0.007156 | 400 |
| E2E总耗时 | 239.653555 | 整体FAIL，不是timeout |

LLM transport耗时与算法stage嵌套，不能相加为总耗时。

## 调用与重试

- logical API calls: 6（Candidate 4，proficiency 2）
- transport attempts: 7
- transport retries: 1（冻结ReliableCompletionClient内部）
- transport response errors: 1，AgenticLLMResponseError
- timeout errors: 0
- verifier/selector contract retries: 0
- identical logical request repeats: 0
- 可核验total tokens: 25,794（失败响应没有可用usage，不声称包含失败计费）
- 已结束单次transport平均耗时: 34.012730秒
- 最慢transport: 70.741936秒
- 总预计完成时间: 不适用；本次已结束。未做额外人工retry或参数调整。

## 接口验收摘要

| Endpoint | HTTP | schema |
|---|---:|---|
| /health | 200 | HealthResponse，无schema_version字段 |
| /api/candidate | 200 | candidate_api_response_v1_1 |
| /api/target-job/133663124 | 200 | target_job_profile_v1.1 |
| /api/match | 200 | matching_pipeline_output_v1 / match_result_v1 |
| /api/learning-path | 400 | 未取得成功schema |

Candidate Skill条目3、grounded capability条目2、evidence3；证据offset回溯原文检查通过。Proficiency 2项、岗位要求7项。
MatchScore=14.29，verified_fit=0.142857，decision=NO_MATCH。
Gap：SATISFIED=1、LEVEL_GAP=0、EVIDENCE_INSUFFICIENT=2、MISSING=4，合计7。
没有保存具体技能标签、预测正文、简历原文、prompt或凭证。此分数仅是运行验收结果，不是准确率。

## Learning Path 400：只读诊断与停止边界

Match与Learning共用两个bridge，但Learning额外执行planner/renderer。本次400正文未持久化，具体抛错行尚未直接捕获，不能虚构确定的错误栈。

发现并用一个单独的合成合同探针复现以下已有冲突（无LLM，不替代真实E2E）：

1. frozen learning_path_stage1.py 的 DeterministicPathPlanner.plan：当无图谱且mode=VERIFY_FIRST时，返回GRAPH_UNAVAILABLE、ordered_steps=()。
2. frozen learning_path_renderer.py 的 _validate_planner_contract：任何VERIFY_FIRST均要求len(ordered_steps)==1；因此抛ValueError：VERIFY_FIRST must contain exactly one planner verification task。
3. _render_skill随即索引ordered_steps[0]，说明不只需调整一个断言，还需明确无图谱展示合同。

这是一项已确认存在的冻结Planner/Renderer合同冲突，也是本次400的合理候选原因，但不等于已证明该次请求走了此分支。修复涉及明确禁止修改的冻结Learning Path/Renderer，故未修改、未添加绕过逻辑、未重放模型或伪造成功结果。

下一步需单独授权只读/离线定位或冻结模块的受控补丁；本轮停止，不自行扩大范围。

## 代码与验证

- 新增backend/runtime_candidate_worker.py、backend/runtime_observability.py：原函数委托计时；不改变参数/结果/模型合同，日志仅允许字段。
- Candidate service：去除同请求重复parse，保留canonical坐标和原900秒限额，错误正文不写日志。
- Proficiency service：仅添加原client/evaluator计时。
- tests/test_api.py调整sidecar测试传输；tests/test_runtime_observability.py新增6项离线测试。
- validation/run_e2e_smoke.py：单次授权、无成功响应复用、只保存结构摘要，真实失败不自动重跑。
- 最终完整回归：283 passed、0 failed、0 skipped，另174 subtests passed（JUnit合计457）。
- Coverage：4308/5260 = 81.90%，原包82.37%；新增运行观测代码计入分母，新worker子进程未合并coverage但未被排除。结果见本轮coverage.xml/validation/pytest_results.xml。

基线ZIP SHA-256保持885fc83be2590ad102f62c78f9ac722e05e0d232e3bc7c1658e99154c47789b9。
candidate_core111文件与job_data4文件逐字节保持不变。没有Gold读取/修改、Blind评测、阈值标定、模型调参或新功能开发。
