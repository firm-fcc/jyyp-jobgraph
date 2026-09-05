from __future__ import annotations

import csv
from functools import lru_cache

from ..config import JD_SUMMARY


@lru_cache(maxsize=1)
def _rows() -> tuple[dict[str, str], ...]:
    with open(JD_SUMMARY, encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


# ---------------------------------------------------------------------------
# 接入侧扩展（本仓库自加，随交付包升级需重新叠加）
#
# 前端由图谱里的岗位节点进入本页，取的是"这个标准岗位下有哪些 JD"，而交付包的
# search_jobs 只有子串检索：按岗位名做子串会把"测试工程师"与"自动化测试工程师"
# 一并取回，两者是体系里的两个岗位。故加一个精确匹配的 std_job 参数。
#
# std_job_index 供前端先判断哪些岗位在本窗口有 JD —— 没有 JD 的岗位不该在
# 选择器里列成可选项，点进去只会拿到一个空列表。
#
# _item 另带 n_prof / salary / work_year 三列：选择器上要显示薪资与年限，
# 这三列本就在同一份汇总表里，不另开一次请求。
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def std_job_index() -> dict[str, int]:
    """标准岗位名 -> 该岗位在本窗口的 JD 条数。前端按岗位取 JD 时先看这里。"""
    counts: dict[str, int] = {}
    for row in _rows():
        name = (row.get("std_job") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _item(row: dict[str, str]) -> dict:
    return {
        "jd_key": row.get("jd_key", ""),
        "jobid": row.get("jobid", ""),
        "title": row.get("title", ""),
        "std_job": row.get("std_job", ""),
        "level": row.get("level", ""),
        "techstack": row.get("techstack", ""),
        "opentime": row.get("opentime", ""),
        "n_skills": int(row.get("n_skills") or 0),
        "n_prof": int(row.get("n_prof") or 0),
        "salary": row.get("salary", ""),
        "work_year": row.get("work_year", ""),
    }


def search_jobs(query: str = "", limit: int = 30, std_job: str = "") -> dict:
    """
    query 为子串检索，沿用交付包的既有语义。
    std_job 为标准岗位名的精确匹配，供前端由岗位节点取该岗位下的 JD 列表；
    两者可同时给出，此时先按 std_job 收窄再按 query 过滤。
    """
    q = query.strip().casefold()
    std = std_job.strip()
    if not 1 <= limit <= 100:
        raise ValueError("limit must be in [1,100]")
    selected = []
    total = 0
    for row in _rows():
        if std and (row.get("std_job") or "").strip() != std:
            continue
        if q:
            haystack = " ".join(
                str(row.get(key, ""))
                for key in ("title", "std_job", "techstack", "level", "jobid")
            ).casefold()
            if q not in haystack:
                continue
        total += 1
        if len(selected) < limit:
            selected.append(_item(row))
    return {
        "schema_version": "job_catalog_response_v1",
        "query": query,
        "std_job": std_job,
        "limit": limit,
        "total": total,
        "items": selected,
    }
