# -*- coding: utf-8 -*-
"""导出图谱运行必需、且不会经 GitHub 同步的数据 → 单个 zip（数据迁移包）。

包内文件一律按**仓库相对路径**存放（正斜杠），恢复 = 在目标机仓库根解压覆盖即可。
路径与恢复说明见 docs/data-migration.md。

选择规则（默认）：
  1) git 忽略集合：`git ls-files --others --ignored --exclude-standard` —— gitignore 覆盖的
     全部数据/缓存/断点/日志（data/、codes/*/output、extractor/cache、classify 断点等，
     这正是 GitHub 拿不到的部分）；
  2) 加上 classify/DeltaG/ 下**未提交**的增量文件（?? 状态，ΔG 状态兜底——建议导出前先
     git commit，使状态经仓库同步）；
  3) 减去显式排除：
     - data/jd_dataset/（6.4GB 源 CSV；图谱运行只需 data/timeline/jd 月度文件，
       需重建时间线时 --include-jd-dataset 加回）
     - codes/api-key.txt（密钥绝不默认入包；--include-api-key 显式加入，注意传输安全）
     - __pycache__ / .pytest_cache / *.pyc / _explore 探索产物 / 本地 Agent 指引
       （CLAUDE.md / AGENTS.md / AGENT-START-HERE.md）。

用法（仓库根运行）：
  python export_graph_bundle.py --dry-run                 # 只统计各级目录体量
  python export_graph_bundle.py                           # 导出 → graph_data_bundle_*.zip
  python export_graph_bundle.py --out D:/migrate/b.zip    # 指定输出路径
  python export_graph_bundle.py --level 1                 # 低压缩级（快，包大）
  python export_graph_bundle.py --include-jd-dataset --include-api-key

产物：graph_data_bundle_YYYYMMDD_HHMM.zip（包内含 MANIFEST.json）
      + 同名 .sha256 校验文件（放包外，与 zip 并列）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# 显式排除（相对路径前缀 / 名称；大小写不敏感匹配前缀或全名）
EXCLUDE_DIRS = ("__pycache__", ".pytest_cache", ".claude", ".agents", ".zed", ".zcode", "_explore")
EXCLUDE_FILES = ("CLAUDE.md", "AGENTS.md", "AGENT-START-HERE.md", "Thumbs.db", "desktop.ini", ".DS_Store")
EXCLUDE_PREFIXES_DEFAULT = ("data/jd_dataset/",)
EXCLUDE_PREFIXES_KEY = ("codes/api-key.txt",)
PROGRESS_EVERY = 50_000


def git_output(*args):
    """运行 git，返回 stdout（bytes，-z 时为 NUL 分隔）。"""
    cmd = ["git", "-c", "core.quotepath=off", *args]
    p = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True)
    if p.returncode != 0:
        raise SystemExit(f"git 命令失败: {' '.join(cmd)}\n{p.stderr.decode('utf-8', 'replace')}")
    return p.stdout


def git_ls(*args):
    raw = git_output(*args, "-z")
    parts = raw.split(b"\0")
    return [p.decode("utf-8", "surrogateescape").replace("\\", "/") for p in parts if p]


def _excluded(path, extra_prefixes):
    parts = path.split("/")
    if any(seg in EXCLUDE_DIRS for seg in parts):
        return True
    name = parts[-1]
    if name in EXCLUDE_FILES or name.endswith(".pyc"):
        return True
    # 自身产物不入包（.gitignore 使旧包成为"忽略文件"，防 zip 套 zip）
    if name.startswith("graph_data_bundle_") and (name.endswith(".zip") or name.endswith(".sha256")):
        return True
    if any(path == pre or path.startswith(pre)
           for pre in EXCLUDE_PREFIXES_DEFAULT + tuple(extra_prefixes)):
        return True
    return False


def collect_files(include_jd_dataset, include_api_key):
    """返回 (待打包相对路径列表, DeltaG 兜底文件集, 未提交未忽略且未入包的文件列表)。"""
    extra = []
    if not include_jd_dataset:
        extra.append(EXCLUDE_PREFIXES_DEFAULT[0])
    if not include_api_key:
        extra.append(EXCLUDE_PREFIXES_KEY[0])

    ignored = set(git_ls("ls-files", "--others", "--ignored", "--exclude-standard"))
    untracked = set(git_ls("ls-files", "--others", "--exclude-standard"))
    # 未提交且未忽略的文件：只兜底收 classify/DeltaG/（ΔG 增量状态）；其余提示用户先提交
    state_picked = {p for p in (untracked - ignored) if p.startswith("classify/DeltaG/")}
    skipped_untracked = sorted(untracked - ignored - state_picked)

    files = sorted(f for f in (ignored | state_picked) if not _excluded(f, extra))
    return files, state_picked, skipped_untracked


def stat_by_root(files):
    """按前两级路径统计 {根: [文件数, 字节数]}。"""
    stats = defaultdict(lambda: [0, 0])
    for f in files:
        parts = f.split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else f
        stats[key][0] += 1
        try:
            stats[key][1] += os.path.getsize(os.path.join(REPO_ROOT, f))
        except OSError:
            pass
    return stats


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description="导出图谱运行数据迁移包（详见 docs/data-migration.md）")
    ap.add_argument("--out", default=None, help="输出 zip 路径（默认仓库根 graph_data_bundle_时间戳.zip）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不打包")
    ap.add_argument("--level", type=int, default=6, help="zip deflate 压缩级 1-9（默认 6；数值越低越快包越大）")
    ap.add_argument("--store", action="store_true", help="不压缩（最快，包最大）")
    ap.add_argument("--include-jd-dataset", action="store_true",
                    help="加回 data/jd_dataset/（6.4GB 源 CSV；仅在目标机需重建时间线/溯源时）")
    ap.add_argument("--include-api-key", action="store_true",
                    help="加回 codes/api-key.txt（密钥入包，传输必须走可信通道并尽快更换）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    print("[1/4] 收集文件清单（git 忽略集 + DeltaG 未提交增量）…")
    files, state_picked, skipped_untracked = collect_files(
        include_jd_dataset=args.include_jd_dataset, include_api_key=args.include_api_key)
    print(f"    待打包 {len(files):,} 个文件"
          f"（含 classify/DeltaG 未提交增量 {len(state_picked)} 个）")
    if skipped_untracked:
        print(f"    [提示] 有 {len(skipped_untracked)} 个未提交且未忽略的文件不在包内（建议先 git commit）：")
        for p in skipped_untracked[:10]:
            print(f"      {p}")
        if len(skipped_untracked) > 10:
            print(f"      …共 {len(skipped_untracked)} 个")

    print("[2/4] 统计体量…")
    stats = stat_by_root(files)
    total_bytes = sum(v[1] for v in stats.values())
    for k in sorted(stats, key=lambda k: -stats[k][1]):
        n, b = stats[k]
        if b:
            print(f"    {human(b):>10}  {n:>8,} 文件  {k}")
    print(f"    合计 {human(total_bytes)}，{len(files):,} 文件")

    if args.dry_run:
        print("（dry-run 未打包）")
        return

    # ---- 打包 ----
    default_name = f"graph_data_bundle_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    zip_path = os.path.abspath(args.out or os.path.join(REPO_ROOT, default_name))
    if not zip_path.lower().endswith(".zip"):
        zip_path += ".zip"

    git_head = git_output("rev-parse", "HEAD").decode().strip()
    git_branch = git_output("rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    dirty = git_output("status", "--porcelain").decode("utf-8", "replace").splitlines()
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "git_head": git_head,
        "git_branch": git_branch,
        "git_uncommitted_entries": len(dirty),
        "purpose": "图谱运行数据迁移包；恢复方式：目标机仓库根解压覆盖（docs/data-migration.md）",
        "flags": {"include_jd_dataset": args.include_jd_dataset,
                  "include_api_key": args.include_api_key,
                  "compression": "store" if args.store else f"deflate-{args.level}"},
        "excludes": ["data/jd_dataset/（除非 --include-jd-dataset）",
                     "codes/api-key.txt（除非 --include-api-key）",
                     "__pycache__/.pytest_cache/*.pyc/_explore/本地 Agent 指引"],
        "state_note": "classify/DeltaG 下未提交的增量文件已兜底入包；恢复后如与仓库 HEAD 冲突，diff 后以较新内容为准",
        "roots": {k: {"files": v[0], "bytes": v[1]} for k, v in sorted(stats.items())},
        "total_files": len(files),
        "total_bytes": total_bytes,
    }

    print(f"[3/4] 打包 → {zip_path}（{manifest['flags']['compression']}）…")
    t0 = time.time()
    comp = zipfile.ZIP_STORED if args.store else zipfile.ZIP_DEFLATED
    failed = []
    with zipfile.ZipFile(zip_path, "w", compression=comp,
                         compresslevel=None if args.store else args.level, allowZip64=True) as z:
        for i, rel in enumerate(files, 1):
            src = os.path.join(REPO_ROOT, *rel.split("/"))
            try:
                z.write(src, rel)
            except OSError as e:
                failed.append(f"{rel}: {e}")
                continue
            if i % PROGRESS_EVERY == 0:
                rate = i / (time.time() - t0 + 1e-6)
                print(f"    {i:,}/{len(files):,}（{rate:,.0f} 文件/秒，"
                      f"预计剩余 {(len(files) - i) / max(rate, 1e-6) / 60:.1f} 分钟）", flush=True)
        z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=1))

    print("[4/4] 计算校验和…")
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with open(zip_path + ".sha256", "w", encoding="ascii") as f:
        f.write(f"{digest}  {os.path.basename(zip_path)}\n")

    size = os.path.getsize(zip_path)
    mins = (time.time() - t0) / 60
    print(f"\n完成：{zip_path}")
    print(f"  大小 {human(size)}（原始 {human(total_bytes)}）| 用时 {mins:.1f} 分钟 | {len(files):,} 文件")
    if failed:
        print(f"  [警告] {len(failed)} 个文件读取失败未入包：")
        for line in failed[:10]:
            print(f"    {line}")
        if len(failed) > 10:
            print(f"    …共 {len(failed)} 个")
    print(f"  SHA256 {digest}（已写 {os.path.basename(zip_path)}.sha256）")
    print(f"  git HEAD {git_head[:12]}（{git_branch}，未提交条目 {len(dirty)}）")
    print("  恢复说明：docs/data-migration.md")


if __name__ == "__main__":
    main()
