"""岗位级的比对基准 —— 由该岗位在本窗口内的全部招聘信息汇总而成。

原先一次匹配只能对一条招聘信息计算：调用方给出 jd_key，适配器取出那一行的
skill_vec_01 与 skill_vec_prof，得到八项上下的要求。换一条招聘信息，要求随之
变一批，达成率也跟着变 —— 求职者要判断的是"我与这个岗位的差距"，而不是"我与
这家公司这一条招聘启事的差距"，逐条比对既不稳定，也无从解释为何取的是这一条。

本模块把同一岗位在窗口内的全部招聘信息合成一份基准，仍走 target_job_profile
v1.1 这一份契约，因而下游的匹配引擎、学习路径与前端一律不必改动。

口径（三条，均可由 /api/job-summary 的原始计数当场验算）：

  纳入   该项能力在本岗位十分之一以上的招聘信息中出现。取十分之一是因为再低
         一档（二十分之一）会把只在个位数条目里出现过的能力也列为岗位要求，
         而本窗口内各岗位的样本量相差两个数量级（二十九条至八百四十八条），
         用比例而非绝对条数才可比。实际落点：各岗位十六至二十一项，
         较单条招聘信息的八项充实，又不至于铺开到四十余项。

  等级   先看这一项有没有资格给出等级：提到它的条目里，写明了熟练度的须过半。
         本窗口内硬技能与软技能在这一点上分得很开 —— 通用程序设计与软件工程
         二百条里有一百九十一条写明了等级，而团队协作协调一百零九条里只有两条。
         拿那两条去定一百零九条的门槛，数是算得出来，但不成立。

         够格的项在写明熟练度的那些条目里取中位数（偶数条时取偏低的一档）：
         过半数的招聘信息要求达到这一级。众数会被某一档的偶然堆积带偏，
         均值则要先把等级当成数值，两者都不如中位数稳当。

         不够格的项记 LEVEL_UNSPECIFIED —— 该岗位普遍要求这项能力，但普遍
         没有写明要到什么程度。这与单条招聘信息下"提到但未标级"的处理一致：
         该项仍参与比对（有没有相应经历），只是不比等级，也不代为拟一个等级。

  辅助   六项辅助能力（AUXILIARY_SKILL_IDS）照旧不参与评级计分，
         状态记 AUXILIARY_NOT_GRADED，与单条招聘信息一致。

合成的是要求的分布，不是新造的事实：每一项都带着它的原始计数
（jd_presence_count / jd_presence_rate / level_distribution），
逐项可回到 jd_summary_2022-10.csv 的对应行。
"""

from __future__ import annotations

from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import (
    CANONICAL_SKILLS,
    JD_SUMMARY,
    JOB_SKILL,
    JOBS,
    PROVIDER_SKILLS,
    WINDOW,
)
from .job_summary_service import JobSummaryNotFoundError, build_job_summary

bootstrap_candidate_core()
from extractor.target_job_profile_adapter import (  # noqa: E402
    AUXILIARY_SKILL_IDS,
    sha256_file,
)

SCHEMA_VERSION = "target_job_profile_v1.1"
SOURCE_TYPE = "aggregated_job"

#: 纳入岗位要求的门槛：该项能力在本岗位多大比例的招聘信息中出现
PRESENCE_RATE_MIN = 0.10

#: 给出要求等级的门槛：提到这项能力的条目里，写明了熟练度的须达到这个比例
GRADED_RATIO_MIN = 0.50

_GRADED_LEVELS = ("P1", "P2", "P3", "P4")


def _median_level(distribution: dict[str, Any]) -> str | None:
    """写明了熟练度的条目里的中位数档位。一条也没有时返回 None。

    档位本身是有序的（P1 < P2 < P3 < P4），故按累计条数走到半数处即可，
    不必把它们折成数值再取平均。偶数条时落在偏低的一档 —— 岗位要求宁可
    报低一档，也不该凭偶数条这一个巧合把门槛抬上去。
    """
    counts = [int(distribution.get(level, 0) or 0) for level in _GRADED_LEVELS]
    total = sum(counts)
    if total <= 0:
        return None
    half = (total + 1) // 2
    seen = 0
    for level, count in zip(_GRADED_LEVELS, counts):
        seen += count
        if seen >= half:
            return level
    return _GRADED_LEVELS[-1]


def build_aggregated_target_job_profile(job_code: str) -> dict[str, Any]:
    """按岗位（而非单条招聘信息）构造比对基准。

    直接建在 /api/job-summary 的统计之上：那一份已经把本岗位窗口内每条招聘
    信息的能力提及与熟练度标注逐行汇总过，此处只做纳入判定与等级归并，
    不再回读一次 CSV。
    """
    summary = build_job_summary(job_code)
    job = summary["job"]
    jd_count = int(job["jd_count"])
    code = str(job["job_code"])
    job_name = str(job["job_name"])

    skills: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for item in summary["skills"]:
        sid = str(item["team_skill_id"])
        rate = float(item["jd_presence_rate"])
        if rate < PRESENCE_RATE_MIN:
            continue

        distribution = dict(item["level_distribution"])
        presence = int(item["jd_presence_count"])
        graded = sum(int(distribution.get(level, 0) or 0) for level in _GRADED_LEVELS)
        graded_ratio = graded / presence if presence else 0.0
        is_primary = sid not in AUXILIARY_SKILL_IDS

        if not is_primary:
            status = "AUXILIARY_NOT_GRADED"
            required_level = None
            target_eligible = False
            level_eligible = False
        elif graded_ratio >= GRADED_RATIO_MIN:
            status = "EXPLICIT_LEVEL"
            required_level = _median_level(distribution)
            target_eligible = True
            level_eligible = True
        else:
            status = "LEVEL_UNSPECIFIED"
            required_level = None
            target_eligible = True
            level_eligible = False
            if graded:
                warnings.append(
                    {
                        "code": "LEVEL_EVIDENCE_BELOW_THRESHOLD",
                        "team_skill_id": sid,
                        "message": (
                            f"Only {graded}/{presence} postings state a level; "
                            "requirement recorded without one."
                        ),
                    }
                )

        skills.append(
            {
                "team_skill_id": sid,
                "team_skill_name": item["team_skill_name"],
                "provider_skill_name": item["team_skill_name"],
                "skill_type": item["skill_type"],
                "is_primary": is_primary,
                "requirement_present": True,
                "required_level_raw": required_level,
                "required_level": required_level,
                "requirement_status": status,
                "learning_path_target_eligible": target_eligible,
                "level_comparison_eligible": level_eligible,
                "requirement_evidence_kind": "STRUCTURED_JD_SUMMARY_PROVENANCE",
                # 前缀沿用单条口径：两者同出于 jd_summary_2022-10.csv，
                # 差别只在取的是一行还是该岗位的全部行，故在段名上标出 aggregated。
                "requirement_evidence_ref": (
                    f"structured_jd_summary:{WINDOW}:{code}:aggregated_skill_vec_01:{sid}"
                ),
                # 这一项的要求从何而来，逐项可当场验算
                "requirement_statistics": {
                    "jd_count": jd_count,
                    "jd_presence_count": item["jd_presence_count"],
                    "jd_presence_rate": item["jd_presence_rate"],
                    "level_distribution": distribution,
                    "graded_posting_count": graded,
                    "graded_ratio": round(graded_ratio, 6),
                    "level_rule": "median_of_graded_postings_when_majority_graded",
                },
                "market_signal": item["market_signal"],
            }
        )

    if not any(s["learning_path_target_eligible"] for s in skills):
        raise JobSummaryNotFoundError(
            f"no aggregated requirement reaches the presence threshold: {code}"
        )

    skills.sort(key=lambda x: x["team_skill_id"])
    warnings.sort(key=lambda x: (x.get("code", ""), x.get("team_skill_id", "")))

    return {
        "schema_version": SCHEMA_VERSION,
        "source_type": SOURCE_TYPE,
        "window": WINDOW,
        "job": {
            "job_code": code,
            "job_name": job_name,
            # 契约要求 jobid 或 jd_key 有值，用作这份基准的标识。岗位级没有单条
            # 招聘信息可指，故以岗位编码充任，并在 aggregated 上标明它的性质。
            "jobid": code,
            "jd_key": None,
            "title": job_name,
            "std_job": job_name,
            "aggregated": True,
            "jd_count": jd_count,
            "opentime": None,
            "level": None,
            "level_source": "aggregated_median",
            "techstack": None,
        },
        "taxonomy": summary["taxonomy"],
        "source_provenance": {
            "jd_summary_sha256": sha256_file(JD_SUMMARY),
            "jobs_sha256": sha256_file(JOBS),
            "job_skill_sha256": sha256_file(JOB_SKILL),
            "provider_taxonomy_sha256": sha256_file(PROVIDER_SKILLS),
            "canonical_taxonomy_sha256": sha256_file(CANONICAL_SKILLS),
            "graph_layer": "effective",
            "raw_jd_evidence_available": False,
            "aggregation": "full_window_std_job_statistics",
            "jd_filter": {"field": "std_job", "value": job_name},
        },
        "semantics": {
            "jd_U": "LEVEL_UNSPECIFIED",
            "jd_U_is_P1": False,
            "market_weight_is_probability": False,
            "market_weight_role": "advisory_only_not_ranked_in_v1.1",
            # 等级是由多条招聘信息的标注归并出来的，不是某一条上写着的原话，
            # 故在语义块里标明，前端据此交代口径。
            "required_level_synthesized": True,
            "required_level_rule": "median_of_graded_postings_when_majority_graded",
            "presence_rate_min": PRESENCE_RATE_MIN,
            "graded_ratio_min": GRADED_RATIO_MIN,
        },
        "skills": skills,
        "warnings": warnings,
    }


__all__ = [
    "GRADED_RATIO_MIN",
    "PRESENCE_RATE_MIN",
    "build_aggregated_target_job_profile",
]
