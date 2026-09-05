# Matching v1 快速接入说明

## Python 调用

```python
from extractor.matching_pipeline_v1 import MatchingPipelineV1
from extractor.team_skill_schema_v3 import CandidateSkillProfile
from backend.config import matching_threshold

candidate = CandidateSkillProfile.from_dict(candidate_skill_profile_json)
result = MatchingPipelineV1(decision_threshold=matching_threshold()).run(
    candidate_profile=candidate,
    target_job_profile=target_job_profile_v1_1_json,
    proficiency_levels={
        # 仅填已经由冻结 proficiency evaluator 得到的真实结果
        # "T-AI-01": "P3",
    },
)

frontend_payload = result.match_result.to_dict()
```

## 当前注意事项

1. 部署环境 `MATCHING_DECISION_THRESHOLD=0.380952` 已冻结；缺失时返回 `None` / `NOT_CALIBRATED`。不允许拍脑袋指定其他阈值。
2. 若 JD 明确要求 P1-P4，而 Candidate 对该 supported Skill 尚未运行 proficiency，结果为 `EVIDENCE_INSUFFICIENT`，不会伪造等级。
3. `partially_supported` 不计入已具备技能。
4. JD `U` 保持 `LEVEL_UNSPECIFIED`，绝不转 P1。
5. 前端只展示 `match_result_v1`，不要使用旧 Demo cosine/五维加权结果冒充正式指标。
