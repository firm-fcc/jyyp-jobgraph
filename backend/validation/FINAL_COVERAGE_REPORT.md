# 最终交付目录Coverage · 2026-09-03

Status: PASS

| 指标 | 实测 |
|---|---:|
| pytest passed | 306 |
| failed / errors / skipped | 0 / 0 / 0 |
| additional subtests passed | 174 |
| statements / covered / missed | 5290 / 4340 / 950 |
| coverage | 82.04% |
| minimum gate | 60% |
| pytest wall time | 9.09秒 |

JUnit展平480条（306+174），不把subtests当额外独立pytest用例。1条Starlette/AnyIO deprecated alias警告，不是失败。
本轮10项前端接入检查；来源296项与174subtests均保留。.coveragerc和pytest.ini未改；来源82.02%，CORS API适配代码进入分母，无人为缩减范围。

Python3.10.11，隔离venv；pytest9.1.1、pytest-cov7.1.0、coverage7.16.0。
在最终交付根目录运行：

```bat
python -B -m pytest -q --cov --cov-report=term:skip-covered --cov-report=xml:coverage.xml --cov-report=html:coverage_html --junitxml=validation/pytest_results.xml
```

机器证据：根coverage.xml、本目录pytest_results.xml。
HTML已在本地生成；最终ZIP不带coverage_html或.coverage缓存，XML及本报告保留。
同一范围内runtime_candidate_worker.py45条、segmented_evidence_extraction_v4.py307条未在子进程覆盖率合并，仍计分母（0%），不冒称全链路分支覆盖。
外部HTTP由tests/conftest.py阻断，不读部署Key，不调用模型。原真实E2E与coverage分开报告。
