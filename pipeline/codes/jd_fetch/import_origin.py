# -*- coding: utf-8 -*-
"""
JD-Origin 老数据导入本地 MySQL（一次性数据搬运脚本，2026-08）。

数据源 data/JD-Origin/：
  - 51job/*.sql                  28 个单表 dump（2023-07 → 2024-09）
  - tencent_cloud_job51.sql.zst  全库 dump（解压 83G），仅流式导入 job 表族
                                  （`job` 基础表 + job_2022_* / job_2023_* 日期分表），
                                  跳过 company / crawl_info 族（无下游消费方）

特性：
  - 断点续跑：output/import_progress.json 记录已完成表与精确行数；表已完整则跳过
  - 完整性判定：dump 头部 AUTO_INCREMENT-1 == 表内 COUNT(*)（dump 生成时无空洞则严格相等）
  - 导入会话禁用 binlog（sql_log_bin=0）+ 临时调大 buffer_pool 提速，结束恢复

用法（在 jd_fetch 目录下）：
  python import_origin.py --scan-only          # 只扫 .zst 表清单/AUTO_INCREMENT，不导入
  python import_origin.py                      # 校验文件夹表（完整则跳过）+ 流式导入 .zst job 族
  python import_origin.py --folder-only        # 只处理文件夹 28 个 dump
  python import_origin.py --zst-only           # 只处理 .zst
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
PROGRESS = os.path.join(OUT_DIR, "import_progress.json")
CONFIG_ORIGIN = os.path.join(HERE, "config_origin.yaml")
ORIGIN = os.path.join(config.PROJECT_ROOT, "data", "JD-Origin")
FOLDER = os.path.join(ORIGIN, "51job")
ZST = os.path.join(ORIGIN, "tencent_cloud_job51.sql.zst")

MYSQL = "/usr/bin/mysql"


def mysql_flags(section):
    """mysql 客户端参数：凭证来自 config（远端/本地通用）+ 导入会话禁 binlog。"""
    return [MYSQL, f"--host={section['ip']}", f"--port={section.get('port', 3306)}",
            f"--user={section['username']}", f"--password={section['password']}",
            "--default-character-set=utf8mb4", "--binary-mode",
            '--init-command=SET SESSION sql_log_bin=0']

# mysqldump 流式过滤器：保留文件头（去掉 CREATE DATABASE/USE，导入目标库由 CLI 指定）
# 与 job 表族段落（`job` 或 `job_*`），丢弃 company/crawl_info 段落。
AWK_FILTER = r'''
function isjob(t) { return t == "job" || index(t, "job_") == 1 }
/^-- Table structure for table `/ || /^-- Dumping data for table `/ {
    t = $0; sub(/^.*table `/, "", t); sub(/`.*/, "", t); keep = isjob(t)
    if ($0 ~ /^-- Table structure/) seen = 1
}
/^(CREATE DATABASE|USE) / { next }
{ if (!seen || keep) print }
'''

# 扫描 .zst 用：输出 "table<TAB>auto_increment" 行（空表无 AUTO_INCREMENT 输出 0）
AWK_SCAN = r'''
/^-- Table structure for table `/ {
    t = $0; sub(/^.*table `/, "", t); sub(/`.*/, "", t); table = t; ai = 0
}
/ AUTO_INCREMENT=[0-9]+ / {
    match($0, /AUTO_INCREMENT=[0-9]+/); ai = substr($0, RSTART + 15, RLENGTH - 15) + 0
}
/^\) ENGINE/ {
    print table "\t" ai; table = ""
}
'''


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect(section):
    return config.connect(section)


def table_counts(cur):
    cur.execute("SHOW TABLES")
    return {r[0] for r in cur.fetchall()}


def exact_count(cur, tbl):
    cur.execute(f"SELECT COUNT(*) FROM `{tbl}`")
    return cur.fetchone()[0]


# ---------- dump 头部解析（文件夹 .sql） ----------

def parse_dump_head(path):
    """返回 (table, auto_increment)；AUTO_INCREMENT 缺失（空表）为 0。只读文件前 64KB。"""
    with open(path, "rb") as f:
        head = f.read(65536).decode("utf-8", errors="replace")
    m = re.search(r"CREATE TABLE `([^`]+)`", head)
    if not m:
        return None, None
    tbl = m.group(1)
    m2 = re.search(r"AUTO_INCREMENT=(\d+)", head)
    return tbl, (int(m2.group(1)) if m2 else 0)


# ---------- 调优 ----------

def apply_tuning(cur):
    """导入提速：8G buffer pool + 组提交放宽。返回原值供恢复。"""
    orig = {}
    for name, val in (("innodb_buffer_pool_size", 8 * 1024 ** 3),
                      ("innodb_flush_log_at_trx_commit", 2),
                      ("sync_binlog", 0)):
        try:
            cur.execute(f"SELECT @@{name}")
            orig[name] = cur.fetchone()[0]
            cur.execute(f"SET GLOBAL {name}={val}")
        except Exception as e:
            log(f"  [warn] 无法设置 {name}: {e}")
    return orig


def restore_tuning(cur, orig):
    for name, val in orig.items():
        try:
            cur.execute(f"SET GLOBAL {name}={val}")
        except Exception as e:
            log(f"  [warn] 无法恢复 {name}: {e}")


# ---------- 文件夹导入 ----------

def import_folder(section, progress):
    files = sorted(f for f in os.listdir(FOLDER) if f.endswith(".sql"))
    log(f"文件夹 dump：{len(files)} 个")
    conn = connect(section)
    cur = conn.cursor()
    done, skipped = 0, 0
    for fname in files:
        path = os.path.join(FOLDER, fname)
        tbl, ai = parse_dump_head(path)
        if not tbl:
            log(f"  [skip] {fname}: 未找到 CREATE TABLE")
            continue
        expected = max(ai - 1, 0)
        if tbl in table_counts(cur):
            cnt = exact_count(cur, tbl)
            if cnt == expected:
                progress["tables"][tbl] = {"rows": cnt, "expected": expected, "source": "folder(已存在,校验通过)"}
                skipped += 1
                continue
            log(f"  {tbl}: 库内 {cnt} != dump 期望 {expected}，重新导入")
        log(f"  导入 {fname}（期望约 {expected} 行）...")
        t0 = time.time()
        r = subprocess.run(mysql_flags(section) + [section["db_name"]], stdin=open(path, "rb"),
                           capture_output=True, text=True)
        if r.returncode != 0:
            log(f"  [error] {fname}: {r.stderr[:300]}")
            continue
        cnt = exact_count(cur, tbl)
        ok = cnt == expected
        progress["tables"][tbl] = {"rows": cnt, "expected": expected, "source": "folder",
                                   "ok": ok, "secs": round(time.time() - t0, 1)}
        save_progress(progress)
        done += 1
        log(f"  {tbl}: {cnt} 行（期望 {expected}）{'✓' if ok else '✗'}")
    conn.close()
    log(f"文件夹完成：新导入 {done}，校验通过跳过 {skipped}")


# ---------- .zst 导入 ----------

def scan_zst(progress):
    """流式扫描 .zst 的表清单与 AUTO_INCREMENT 估算（一次解压，约 3-6 分钟）。"""
    log("扫描 .zst 表清单（流式，不落盘）...")
    t0 = time.time()
    p1 = subprocess.Popen(["zstdcat", "-T0", ZST], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["awk", AWK_SCAN], stdin=p1.stdout, stdout=subprocess.PIPE, text=True)
    p1.stdout.close()
    tables = {}
    for line in p2.stdout:
        tbl, ai = line.rstrip("\n").split("\t")
        tables[tbl] = int(ai)
    p2.wait(); p1.wait()
    job_tables = {t: a for t, a in tables.items() if t == "job" or t.startswith("job_")}
    log(f"扫描完成（{time.time()-t0:.0f}s）：共 {len(tables)} 表，job 族 {len(job_tables)} 表，"
        f"估算行数 {sum(max(a-1,0) for a in job_tables.values()):,}")
    progress["zst_scan"] = {"all_tables": len(tables), "job_tables": job_tables}
    save_progress(progress)
    return job_tables


def monitor_import(section, stop, progress_log):
    """后台线程：每 30s 打印已出现表的近似行数，观察导入进度。"""
    while not stop.is_set():
        stop.wait(30)
        if stop.is_set():
            break
        try:
            conn = connect(section)
            cur = conn.cursor()
            cur.execute("SELECT table_name, table_rows FROM information_schema.tables "
                        f"WHERE table_schema='{section['db_name']}'")
            rows = {r[0]: r[1] for r in cur.fetchall()}
            conn.close()
            total = sum(v or 0 for v in rows.values())
            log(f"  [进度] 库内 {len(rows)} 表，累计约 {total:,} 行")
        except Exception:
            pass


def import_zst(section, progress, job_tables):
    if not job_tables:
        job_tables = progress.get("zst_scan", {}).get("job_tables", {})
    expected_total = sum(max(a - 1, 0) for a in job_tables.values())
    log(f"流式导入 .zst job 族（{len(job_tables)} 表，估算 {expected_total:,} 行）...")
    t0 = time.time()
    stop = threading.Event()
    mon = threading.Thread(target=monitor_import, args=(section, stop, progress), daemon=True)
    mon.start()
    p1 = subprocess.Popen(["zstdcat", "-T0", ZST], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["awk", AWK_FILTER], stdin=p1.stdout, stdout=subprocess.PIPE)
    p3 = None
    try:
        p3 = subprocess.run(mysql_flags(section) + [section["db_name"]], stdin=p2.stdout,
                            capture_output=True, text=True)
    finally:
        p2.stdout.close()
        if p3 is None or p3.returncode != 0:
            p2.kill()
            p1.kill()  # mysql 失败时上游不 kill 会因管道阻塞永久挂起
        p2.wait()
        p1.wait()
    stop.set()
    log(f"管道结束（{time.time()-t0:.0f}s）：mysql exit={p3.returncode}")
    if p3.returncode != 0:
        log(f"[error] mysql: {p3.stderr[:500]}")

    # 逐表精确校验
    conn = connect(section)
    cur = conn.cursor()
    ok = bad = 0
    for tbl, ai in sorted(job_tables.items()):
        expected = max(ai - 1, 0)
        try:
            cnt = exact_count(cur, tbl)
        except Exception as e:
            log(f"  [error] {tbl}: {e}")
            continue
        good = cnt == expected
        progress["tables"][tbl] = {"rows": cnt, "expected": expected, "source": "zst", "ok": good}
        ok += good
        bad += (not good)
        if not good:
            log(f"  [mismatch] {tbl}: {cnt} != {expected}")
    conn.close()
    progress["zst_verify"] = {"ok": ok, "mismatch": bad, "secs": round(time.time() - t0, 1)}
    save_progress(progress)
    log(f".zst 校验：{ok} 表一致，{bad} 表不符")


# ---------- 进度与收尾 ----------

def load_progress():
    if os.path.exists(PROGRESS):
        d = json.load(open(PROGRESS, encoding="utf-8"))
    else:
        d = {"tables": {}}
    d.setdefault("tables", {})
    return d


def save_progress(progress):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def write_tables_to_config(section, progress):
    """把本地库最终 job 表清单回填 config_origin.yaml。"""
    conn = connect(section)
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    tables = sorted(r[0] for r in cur.fetchall())
    conn.close()
    with open(CONFIG_ORIGIN, encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r"tables: \[.*?\]|tables: \[\]", "tables: [" + ", ".join(f'"{t}"' for t in tables) + "]",
                     content, flags=re.S)
    with open(CONFIG_ORIGIN, "w", encoding="utf-8") as f:
        f.write(content)
    log(f"config_origin.yaml tables 回填完成：{len(tables)} 张表")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-only", action="store_true", help="只扫描 .zst 表清单，不导入")
    ap.add_argument("--folder-only", action="store_true")
    ap.add_argument("--zst-only", action="store_true")
    args = ap.parse_args()

    section = config.load_config(CONFIG_ORIGIN)
    conn = connect(section)
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{section['db_name']}` DEFAULT CHARACTER SET utf8mb4")
    conn.close()

    progress = load_progress()

    if args.scan_only:
        scan_zst(progress)
        return

    conn = connect(section)
    cur = conn.cursor()
    orig = apply_tuning(cur)
    conn.close()
    try:
        if not args.zst_only:
            import_folder(section, progress)
        if not args.folder_only:
            job_tables = scan_zst(progress) if "zst_scan" not in progress else progress["zst_scan"]["job_tables"]
            import_zst(section, progress, job_tables)
    finally:
        conn = connect(section)
        restore_tuning(conn.cursor(), orig)
        conn.close()
    write_tables_to_config(section, progress)
    log("全部完成。")


if __name__ == "__main__":
    main()
