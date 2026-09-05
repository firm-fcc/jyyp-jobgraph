# 前端API参考（当前交付源码合同）

本文件只描述本包`app.py`、`backend/schemas.py`、services及冻结serializer。完整前端类型见`frontend_api_types.ts`。所有下方JSON均是**结构示例/字段节选，不是模型结果、Gold或准确率证据**；节选不能直接作为完整请求复用。实际链路把前一接口完整对象传给下一接口。

## 共通约定

- 七类业务接口见下表。JSON键名区分大小写；身份字段为`team_skill_id`，不以中文名关联。
- 后端先读取进程环境，再从根目录`.env`补缺失变量；`.env`不打包。CORS来源为启动时配置，修改后需重启。
- 多数业务端点直接返回字典，OpenAPI的响应body schema较宽；`/health`和请求参数/请求schema有明确OpenAPI描述。不要仅靠Swagger的空响应schema推测字段，须同时使用本文件/TypeScript和serializer。OpenAPI可生成不代表其已穷尽所有输出字段。
- 大多数成功响应有`schema_version`；Health没有。`null`与缺字段不同；`0`、空数组也不等于接口错误。
- 常见错误：`{"detail":"错误说明"}`；422的`detail`是含`loc/msg/type`的数组。不要将原始错误对象当HTML插入页面，也不要公开记录简历正文或模型内容。
- 400=业务/合同不合法；422=请求参数/schema不合法；502=受控运行/模型技术失败。网络/CORS/5xx不得自动重发付费POST。其他未预期5xx应反馈部署者，不能fallback Mock。

| 方法 | URL | 请求 | 成功类型 |
|---|---|---|---|
| GET | /health | 无 | HealthResponse |
| GET | /api/jobs | q、limit | JobsResponse |
| POST | /api/candidate | multipart file、candidate_id；query开关 | CandidateResponse或CandidatePreflightResponse |
| GET | /api/target-job/{job_id} | 可选jd_key | TargetJobResponse |
| GET | /api/job-summary/{job_code} | 路径job_code | JobSummaryResponse |
| POST | /api/match | MatchRequest JSON | MatchResponse |
| POST | /api/learning-path | LearningPathRequest JSON | LearningPathResponse |

## 1. GET /health：启动门禁

请求：`GET /health`，无body。响应结构示例（未配置LLM）：

```json
{"status":"ok","service":"challenge26-backend-handoff","candidate_runtime":"r4.3.4","target_job_schema":"target_job_profile_v1.1","matching_schema":"match_result_v1","matching_calibrated":true,"llm_configured":false,"window":"2022-10","limitations":{"matching_decision":"CALIBRATED","learning_path_curated_graph_count":6,"job_window":"2022-10","matching_threshold_invalid":false}}
```

上述字段均返回；无schema_version，无nullable字段。读`status/llm_configured/matching_calibrated/limitations`。
`llm_configured=false`说明真实模型提取不可用，纯文档预检仍可用。readiness不发模型请求，true也不保证供应商可用。
`matching_calibrated=false`不得展示正式MATCH/NO_MATCH；非法阈值时health仍200，并置`matching_threshold_invalid=true`。本包阈值应为0.380952。

## 2. GET /api/jobs：选择单条JD

请求示例：`GET /api/jobs?q=算法&limit=5`。`q`可省略（空字符串）；`limit`可省略（30），范围1–100，不存在分页参数。

```json
{"schema_version":"job_catalog_response_v1","query":"算法","limit":5,"items":[]}
```

空items为合法无结果结构示例。每个实际item必有`jd_key/jobid/title/std_job/level/techstack/opentime/n_skills`，前7项为string，n_skills为number。保留jobid与jd_key，标题仅展示；无nullable字段，缺原数据时字符串可空。
422：limit越界/非法；400：数据合同问题。下一个接口按jobid或jd_key取完整TargetJobProfile。

## 3. POST /api/candidate：真实文档能力提取

multipart字段：`file`必填；`candidate_id`可省略，服务会生成/清洗安全ID。支持PDF、DOCX、TXT；**不支持旧DOC，也没有OCR能力承诺**。前端应提示先转换DOC或扫描件。
Query：`preflight=false`、`allow_low_quality_parser=false`（默认）。前者true只做本地解析预检、不调用LLM；后者不是常规前端开关，不要为绕过低质量文档擅自开启。

```ts
const body = new FormData();
body.append("file", file);
const response = await fetch(`${base}/api/candidate`, {method: "POST", body});
```

不要手工设置multipart Content-Type。结构示例（空集合仅说明容器形状，不是能力判断）：

```json
{"schema_version":"candidate_api_response_v1_1","candidate_id":"structure_example","candidate_skill_profile":{"candidate_id":"structure_example","skill_registry_version":"0.4","assessments":[],"metadata":{}},"explicit_skill_mentions":[],"diagnostics":{},"grounded_capability_candidates":[],"resume_text":"结构示例文本","source_segments":[],"experience_metadata_available":false,"runtime_schema":"resume_capability_v3_run_ready_r4_3_4","proficiency_status":"not_run_in_preuse_entrypoint"}
```

所有顶层字段均返回，runtime_schema可null；数组可以空。正式能力卡读`candidate_skill_profile.assessments`：team_skill_id/name、status（supported/partially_supported/unsupported）、evidence、reason、confidence（number或null）、atomic_abilities、audit_flags。没有顶层`skills`或最终P等级字段。

能力到原文高亮：使用assessment.evidence的`start/end/text/source_experience_id`，坐标是`resume_text`的Python Unicode码点索引，左闭右开。不要用PDF页码代替，也不要改写resume_text。JS含emoji等非BMP字符时使用：

```ts
const quote = Array.from(candidate.resume_text).slice(start, end).join("");
if (quote !== evidence.text) { /* 禁止画假高亮，提示证据定位异常 */ }
```

Evidence的start/end必须同时数字或同时null；正向supported/partially_supported证据由合同要求定位。fact/behavior/context/result可为空字符串，不得补写。
`source_segments`条目为source_experience_id/section_type/start/end/text；无可靠分段时数组为空且experience_metadata_available=false，**不声称已可靠切出经历**。
`grounded_capability_candidates`是原文已定位的细粒度诊断线索，hint_authority=non_authoritative_llm_annotation，不是额外正式Team Skill标签。`explicit_skill_mentions`只展示，不升级能力支持状态。

400：不支持文件类型/空文件/解析值错误；413：超过MAX_RESUME_BYTES（默认10485760）；422：缺file或非法query；502：受控提取失败/900秒子进程超时。未受控异常可能为500，不能伪造结果。preflight=true返回不同schema`candidate_preflight_v1`（parser、quality、team_skill_registry_version、team_skill_count），不能当CandidateResponse送Matching。

本接口长耗时：上传即Loading、禁重复、显示“正在解析简历并评估能力，可能需要数分钟”。不要自动重发或设置过短超时；用户主动取消只终止UI等待，不保证服务器已终止。当前无异步队列/进度推送。

## 4. GET /api/target-job/{job_id}：单JD目标画像

请求：`GET /api/target-job/133663124`。如果jobid有歧义，使用列表给出的`jd_key`：`GET /api/target-job/133663124?jd_key=<URL编码值>`，此时jd_key优先选择，路径job_id被忽略。
响应字段节选（结构示例）：

```json
{"schema_version":"target_job_profile_v1.1","source_type":"single_jd","job":{"jobid":"133663124","jd_key":"结构示例","title":"结构示例岗位"},"semantics":{"jd_U":"LEVEL_UNSPECIFIED","jd_U_is_P1":false,"market_weight_is_probability":false,"market_weight_role":"advisory_only_not_ranked_in_v1.1"}}
```

完整必返字段：schema_version/source_type/window/job/taxonomy/source_provenance/semantics/skills/warnings。job含job_code/job_name/jd_key/jobid/title/std_job/opentime/level/level_source/techstack；部分CSV元数据可null（详见TS），不补虚构标题。
taxonomy给provider/canonical版本及hash、双taxonomy兼容结果、identity_rule=team_skill_id。

每个skill的关键字段：team_skill_id/name、is_primary、required_level_raw、required_level、requirement_status、learning_path_target_eligible、level_comparison_eligible、requirement_evidence_kind/ref、market_signal。

| requirement_status | required_level | 进入目标 | 比较等级 |
|---|---|---|---|
| EXPLICIT_LEVEL | P1–P4 | true | true |
| LEVEL_UNSPECIFIED | null（raw=U） | true | false |
| PROFICIENCY_NOT_AVAILABLE | null | false | false |
| AUXILIARY_NOT_GRADED | null | false | false |

market_signal可null；其权重不是概率，不用于本版rank。requirement evidence是结构化JD来源引用，**不是raw JD原句**。完整Target对象原样传Match，不用job-summary替换。
409：jobid/jd_key未匹配到唯一一行（包含0行或多行）；400：其他taxonomy/数据合同错误；422：参数结构错误。

## 5. GET /api/job-summary/{job_code}：聚合展示

请求：`GET /api/job-summary/AID-01`。job_code是标准岗位类别码，不是jobid。
响应节选（结构示例）：

```json
{"schema_version":"aggregated_job_summary_v1","source_type":"aggregated_job_summary","window":"2022-10","job":{"job_code":"AID-01","job_name":"结构示例岗位","jd_count":1},"semantics":{"matching_input":false,"matching_decision_available":false,"required_level_synthesized":false,"jd_U":"LEVEL_UNSPECIFIED","jd_U_is_P1":false,"market_weight_is_probability":false}}
```

完整必返job/skills/taxonomy/provenance/semantics。skills每项包含team_skill_id/name、skill_type、is_primary、jd_presence_count/rate、level_distribution（P1–P4/U/NOT_AVAILABLE全部计数）、market_signal；权重字段可null。用于图表展示，不合成required_level，不输入Matching或Learning Path。404：未知岗位码/窗口无该类JD；400：底层结构不合法；422：参数错误。

## 6. POST /api/match：正式单JD匹配

JSON请求的candidate_profile必填，必须取Candidate响应内的candidate_skill_profile，不是整个CandidateResponse。
三种Target选择方式互斥：完整target_job_profile；或者job_id；或者jd_key。不要同时给多个。
proficiency_levels可省略/null（auto_proficiency默认true，可能真实付费调用）；传入已有合法levels对象（即使{}）则复用，不调用模型。auto_proficiency=false且未提供等级会保留未知，不自动填P1。

```ts
const request = {
  candidate_profile: candidate.candidate_skill_profile,
  target_job_profile: target,
  auto_proficiency: true
};
```

响应字段节选（只展示路径，不是完整MatchResult）：

```json
{"schema_version":"matching_pipeline_output_v1","match_result":{"schema_version":"match_result_v1","match_score":0,"decision":"NO_MATCH","decision_threshold":0.380952,"metrics":{"verified_fit":0,"skill_coverage":0,"level_gap_rate":0,"uncertainty_rate":0,"missing_rate":1},"summary":{"required_skills":1,"satisfied":0,"level_gap":0,"evidence_insufficient":0,"missing":1}},"proficiency":{"source":"provided","levels":{},"details":[]}}
```

完整顶层必返match_result/diagnostics/target_job_profile/proficiency。MatchResult另含candidate_id/job_id/job_title/skills/semantics。
准确路径：`match_result.match_score`（0–100）、`match_result.metrics.verified_fit`（0–1）、`match_result.decision`。不是result顶层metrics/summary/match_score。
decision仅MATCH/NO_MATCH/NOT_CALIBRATED；threshold可null，正式配置时必须0.380952。前端只展示后端decision，不自己按百分数或四舍五入分数重判。

| skills[].gap_type | 中文 | 展示边界 |
|---|---|---|
| SATISFIED | 已满足 | 当前证据与目标下满足 |
| LEVEL_GAP | 熟练度不足 | 观察等级低于岗位要求，不是人格评价 |
| EVIDENCE_INSUFFICIENT | 证据不足 | 存在能力证据但等级不足以判断，不等于MISSING |
| MISSING | 能力缺失 | 当前证据未覆盖岗位要求，不表示候选人不会 |

逐skill保留required_level/candidate_level（可null）、path_mode、requirement_evidence、candidate_evidence、explanation。Match证据字段为source_id/evidence_ref，与Candidate的source_experience_id不同。
proficiency含source/levels/details；details是既有评估详情，不需前端复算。Candidate U为证据不足；JD U为等级未指定，均不等于P1。
400：非法Profile、互斥选择器、等级/阈值/合同；422：请求Schema；502：受控模型/运行失败。

## 7. POST /api/learning-path：差距改进路径

请求结构与MatchRequest一致。**正常接入必须复用Match等级**：

```ts
const request = {
  candidate_profile: candidate.candidate_skill_profile,
  target_job_profile: match.target_job_profile,
  proficiency_levels: match.proficiency.levels,
  auto_proficiency: false
};
```

响应字段节选（结构示例；skills等完整内容按TS）：

```json
{"schema_version":"learning_path_api_response_v1","path_status":"PARTIAL_GRAPH_COVERAGE","proficiency":{"source":"provided","levels":{},"details":[]}}
```

上例省略gap_summary、rendered及diagnostics，只展示顶层路径与等级复用形状，不是完整响应或验收结果。完整rendered.skill_paths按每项岗位要求返回对应业务状态。
完整顶层必返schema_version/path_status/gap_summary/rendered/proficiency/diagnostics。gap_summary键为**大写**，与Match.summary小写不同。
顶层path_status仅READY/PARTIAL_GRAPH_COVERAGE/NO_ACTION；`rendered.skill_paths[].path_status`才是逐能力业务状态：

| 逐skill path_status | 前端显示 |
|---|---|
| READY | 正常展示learning_steps、verification_guidance或capstone_guidance |
| GRAPH_UNAVAILABLE | 当前暂未配置该能力的发展路径；合法HTTP200，不是服务器异常 |
| NO_ACTION | 当前能力已满足岗位要求，无需额外学习 |

GRAPH_UNAVAILABLE保留team_skill_id、gap_type、path_mode、gap_explanation；learning_steps为空，verification/capstone为null，不伪造任务、不改为NONE。
有图谱的VERIFY_FIRST通常learning_steps仍为空，但verification_guidance有验证任务；不要只看steps数量判失败。部分READY路径仅有capstone，按实际字段展示。
render_status=READY表示渲染结构就绪，不等于有图谱；优先读path_status。observed_level/required_level/reassessment_guidance、evidence_task及guidance对象可null。
reassessment_required明确后续需重新评估；学习完成不自动升P等级。保留顺序与节点，不自行选择图谱或追加步骤。
400：非法skill/请求内合同；422：外层请求Schema；502：受控运行异常。**无graph本身不能当400**。

## 前端错误与交互底线

不自行计算MatchScore，不改threshold，不使用cosine替代，不fallback Mock，不写死演示准确率。健康门禁不通过时停止真实链。
CORS不是鉴权；公开部署仍需TLS/访问控制/资源限制。前端与代理不要配置短超时，不自动retry Candidate/Match/Learning POST。
当前已保留来源包真实E2E全部200证据；本轮仅本地确定性接口与CORS测试，没有重跑LLM。Browser React Integration = PENDING。
