# -*- coding: utf-8 -*-
"""unit-tests 一键运行器：pytest 全量 + 覆盖率统计 + 逐用例结果 → 四份报告。

用法（仓库根目录）：
  python unit-tests/run_tests.py            # 跑全部用例并生成报告
  python unit-tests/run_tests.py -v         # 透传 pytest 详细输出

产物（均随仓库提交，可重跑核对）：
  unit-tests/coverage-report.md    分包/分模块覆盖率表
  unit-tests/test-results.md       逐用例通过状态与耗时
  unit-tests/TEST-CASES.md         逐用例输入构造/期望输出/属性明细（数据源 case_catalog.py）
  unit-tests/test-cases.csv        汇总表：输入/期望输出/属性 + 运行结果/耗时/结果说明
                                    （结果说明列合并 test_llm_live 的实测观察）

中间数据（不入库）：.coverage.json / .test-results.xml / .live-observations.json

说明：默认 3 例 LLM 真调用冒烟（test_llm_live.py）需要 codes/api-key.txt，
无密钥时自动跳过；离线 175 例不受影响。退出码 = pytest 退出码。
"""
import ast
import csv
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import case_catalog  # noqa: E402  用例目录（单一事实源）

PACKAGES = ["codes/graph", "codes/builder", "codes/extractor", "codes/jd_annotate"]
JSON_OUT = os.path.join(HERE, ".coverage.json")
XML_OUT = os.path.join(HERE, ".test-results.xml")
OBS_JSON = os.path.join(HERE, ".live-observations.json")
REPORT_MD = os.path.join(HERE, "coverage-report.md")
RESULTS_MD = os.path.join(HERE, "test-results.md")
CASES_MD = os.path.join(HERE, "TEST-CASES.md")
CASES_CSV = os.path.join(HERE, "test-cases.csv")

DEFAULT_NOTE_PASS = "断言全部通过，输出与期望一致"
DEFAULT_NOTE_SKIP = "无密钥自动跳过（离线套件不受影响）"


# ---------------- JUnit 解析（结果/耗时共用） ----------------

def parse_junit():
    """→ {用例名: (状态, 耗时)} 与按文件分组列表。"""
    root = ET.parse(XML_OUT).getroot()
    by_case, by_file = {}, defaultdict(list)
    for ts in root.iter("testsuite"):
        for tc in ts.iter("testcase"):
            fn = (tc.get("classname", "").split(".")[-1] + ".py") or "?"
            name = tc.get("name", "?")
            t = float(tc.get("time", "0") or 0)
            if tc.find("failure") is not None:
                status = "失败"
            elif tc.find("error") is not None:
                status = "错误"
            elif tc.find("skipped") is not None:
                status = "跳过"
            else:
                status = "通过"
            by_case[name] = (status, t)
            by_file[fn].append((name, status, t))
    return by_case, by_file


# ---------------- 覆盖率报告 ----------------

def write_coverage_report():
    data = json.load(open(JSON_OUT, encoding="utf-8"))["files"]
    rows = []
    for path, info in data.items():
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        st = info["summary"]["num_statements"]
        miss = info["summary"]["missing_lines"]
        if st == 0:
            continue
        rows.append((rel, st, miss))
    rows.sort()
    total_st = sum(r[1] for r in rows)
    total_miss = sum(r[2] for r in rows)
    pct = (total_st - total_miss) / total_st if total_st else 0.0

    by_pkg = {}
    for rel, st, miss in rows:
        pkg = rel.split("/")[1]
        s, m = by_pkg.get(pkg, (0, 0))
        by_pkg[pkg] = (s + st, m + miss)

    lines = [
        "# 单元测试覆盖率报告（自动生成，勿手改）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 用例：`python unit-tests/run_tests.py`（pytest 全量，零 LLM / 零网络）",
        f"- 口径：codes/ 四包 {len(rows)} 个核心模块（排除清单见 `.coveragerc` 与 README §五）",
        "",
        "## 总览",
        "",
        f"| 指标 | 数值 |",
        f"| --- | --- |",
        f"| 覆盖率（语句） | **{pct:.1%}**（{total_st - total_miss:,}/{total_st:,}） |",
    ]
    for pkg in sorted(by_pkg):
        s, m = by_pkg[pkg]
        lines.append(f"| codes/{pkg} | {(s - m) / s:.1%}（{s - m:,}/{s:,}） |")
    lines += ["", "## 分模块明细", "",
              "| 模块 | 语句 | 未覆盖 | 覆盖率 |", "| --- | ---: | ---: | ---: |"]
    for rel, st, miss in rows:
        lines.append(f"| `{rel}` | {st} | {miss} | {(st - miss) / st:.1%} |")
    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return pct, total_st, total_miss, len(rows)


# ---------------- 逐用例结果报告 ----------------

def write_results_report(by_file):
    counts = defaultdict(int)
    for cases in by_file.values():
        for _, status, _ in cases:
            counts[status] += 1
    n_pass, total = counts["通过"], sum(counts.values())
    others = "".join(f"，{k} {v}" for k, v in counts.items() if k != "通过")
    lines = [
        "# 单元测试运行结果（自动生成，勿手改）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 命令：`python unit-tests/run_tests.py`（离线用例零 LLM / 零网络；"
        f"test_llm_live 3 例为真调用冒烟，无密钥自动跳过）",
        f"- 结果：**{n_pass}/{total} 通过**{others}",
        "",
        "逐用例状态与耗时（用例的输入/期望输出明细见 `TEST-CASES.md` 与 `test-cases.csv`）：",
        "",
    ]
    for fn in sorted(by_file):
        cases = by_file[fn]
        ok = all(s == "通过" for _, s, _ in cases)
        lines.append(f"## {fn}（{len(cases)} 例{'，全部通过' if ok else '，存在未通过'}）")
        lines.append("")
        lines.append("| 用例 | 结果 | 耗时 |")
        lines.append("| --- | --- | ---: |")
        for name, status, t in cases:
            lines.append(f"| `{name}` | {status} | {t:.2f}s |")
        lines.append("")
    with open(RESULTS_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return n_pass, total


# ---------------- 用例明细（TEST-CASES.md + test-cases.csv） ----------------

def _load_live_notes():
    if os.path.exists(OBS_JSON):
        try:
            return json.load(open(OBS_JSON, encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _ordered_catalog():
    """目录按源码函数定义顺序排列（与 test-results.md 对齐）。"""
    out = []
    for fn, comp, desc, cases in case_catalog.FILES:
        try:
            tree = ast.parse(open(os.path.join(HERE, fn), encoding="utf-8").read())
            order = {n.name: i for i, n in enumerate(tree.body)
                     if isinstance(n, ast.FunctionDef)}
            cases = sorted(cases, key=lambda c: order.get(c[0], 10 ** 6))
        except (OSError, SyntaxError):
            pass
        out.append((fn, comp, desc, cases))
    return out


def write_cases_md():
    total = 0
    parts = ["# 单元测试用例明细（TEST-CASES）",
             "",
             "逐用例列出**输入构造**（夹具/注入/参数）与**期望输出**（关键断言值）。与 "
             "`unit-tests/README.md` 的组件地图对应：README 回答“测哪些组件”，本表回答"
             "“每个用例具体怎么测、期望什么”；单表汇总（含运行结果与结果说明）见 "
             "`test-cases.csv`。属性标签：数学手算＝公式逐值断言；确定性＝同种子同结果；"
             "幂等＝重复执行收敛；容错降级＝故障输入不中断不丢信号；防线回归＝历史缺陷固化用例；"
             "协议契约＝接口格式/口径；边界＝极端与空值；读写字环＝落盘回读一致；并发安全；"
             "端到端产物＝阶段产物文件级校验。`test_llm_live.py` 为真实 API 小批量冒烟"
             "（无密钥自动跳过），其余全部离线。",
             ""]
    for fn, comp, desc, cases in _ordered_catalog():
        total += len(cases)
        parts.append(f"## {fn}（{len(cases)} 例）")
        parts.append("")
        parts.append(f"**被测**：{comp} —— {desc}。" if comp else f"**被测**：{desc}。")
        parts.append("")
        parts.append("| 用例 | 输入 | 期望输出 | 属性 |")
        parts.append("| --- | --- | --- | --- |")
        for name, _doc, inp, out, tag in cases:
            parts.append(f"| `{name}` | {inp} | {out} | {tag} |")
        parts.append("")
    parts.append(f"共 {total} 例（运行结果见 `test-results.md`，覆盖率见 `coverage-report.md`）。")
    with open(CASES_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts) + "\n")
    return total


def write_cases_csv(by_case):
    notes = _load_live_notes()
    total = 0
    with open(CASES_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["测试文件", "用例", "被测组件", "输入", "期望输出", "属性",
                    "运行结果", "耗时(秒)", "结果说明"])
        for fn, comp, desc, cases in _ordered_catalog():
            for name, _doc, inp, out, tag in cases:
                total += 1
                status, t = by_case.get(name, ("未运行", 0.0))
                if name in notes:
                    note = notes[name]
                elif status == "通过":
                    note = DEFAULT_NOTE_PASS
                elif status == "跳过":
                    note = DEFAULT_NOTE_SKIP
                else:
                    note = "见 pytest 输出失败详情"
                w.writerow([fn, name, comp or "", inp, out, tag,
                            status, f"{t:.2f}", note])
    return total


def main():
    argv = [sys.executable, "-m", "pytest", HERE, "-q", "--cov-config",
            os.path.join(HERE, ".coveragerc"), "--cov-report=json:" + JSON_OUT,
            "--junitxml=" + XML_OUT]
    for p in PACKAGES:
        argv += ["--cov", p]
    if "-v" in sys.argv:
        argv.remove("-q"), argv.remove("-v"), argv.insert(argv.index(HERE), "-v")
    print(">>>", " ".join(argv), flush=True)
    rc = subprocess.call(argv, cwd=REPO)

    by_case = by_file = None
    if os.path.exists(XML_OUT):
        by_case, by_file = parse_junit()
        n_pass, total = write_results_report(by_file)
        print(f"[run_tests] 用例结果 {n_pass}/{total} 通过 → {os.path.relpath(RESULTS_MD, REPO)}")
    if os.path.exists(JSON_OUT):
        pct, ts, tm, n_mod = write_coverage_report()
        print(f"[run_tests] 口径覆盖率 {pct:.1%}（{ts - tm}/{ts}，{n_mod} 模块）"
              f"→ {os.path.relpath(REPORT_MD, REPO)}")
    if by_case is not None:
        n_md = write_cases_md()
        n_csv = write_cases_csv(by_case)
        print(f"[run_tests] 用例明细 {n_md} 例 → TEST-CASES.md；汇总表 {n_csv} 行 → test-cases.csv")
    sys.exit(rc)


if __name__ == "__main__":
    main()
