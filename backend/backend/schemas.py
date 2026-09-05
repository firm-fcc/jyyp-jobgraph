from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


#: 熟练度评级的取值范围。
#:
#: ``target``    只给目标岗位要求的那几项定级，与逐条招聘信息比对时的做法一致。
#: ``candidate`` 给候选人全部已具备的能力定级，与目标岗位无关。
#:
#: 定级要逐项调模型，一项数十秒。按 ``target`` 取值时，换一个目标岗位就换了一批
#: 待定级的项，于是每换一次岗位都要重跑一遍；按 ``candidate`` 取值则一次算齐，
#: 此后换岗位只需把已得的档位随请求带上并关掉自动定级，匹配本身是纯规则运算，
#: 即时可得。前端的岗位切换走的是后一条路。
ProficiencyScope = Literal["target", "candidate"]


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_profile: dict[str, Any]
    target_job_profile: dict[str, Any] | None = None
    job_id: str | None = None
    jd_key: str | None = None
    #: 标准岗位编码（AID-01）。给出它即按该岗位窗口内全部招聘信息的汇总为基准，
    #: 与 job_id / jd_key / target_job_profile 四者取其一。
    job_code: str | None = None
    proficiency_levels: dict[str, Literal["P1", "P2", "P3", "P4", "U"]] | None = None
    auto_proficiency: bool = True
    proficiency_scope: ProficiencyScope = "target"


class LearningPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_profile: dict[str, Any]
    target_job_profile: dict[str, Any] | None = None
    job_id: str | None = None
    jd_key: str | None = None
    job_code: str | None = None
    proficiency_levels: dict[str, Literal["P1", "P2", "P3", "P4", "U"]] | None = None
    auto_proficiency: bool = True
    proficiency_scope: ProficiencyScope = "target"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "challenge26-backend-handoff"
    candidate_runtime: str = "r4.3.4"
    target_job_schema: str = "target_job_profile_v1.1"
    matching_schema: str = "match_result_v1"
    matching_calibrated: bool
    llm_configured: bool
    window: str = "2022-10"
    limitations: dict[str, Any] = Field(default_factory=dict)
