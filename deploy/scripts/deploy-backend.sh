#!/usr/bin/env bash
#
# 后端发布：上传代码与运行时数据、同步依赖、重启服务。在仓库根的宿主机执行。
#
#   bash deploy/scripts/deploy-backend.sh root@1.2.3.4
#
# 服务器上的 .env 由部署者手工维护，本脚本一律不覆盖，亦不上传本地 .env。
# 首次执行前须先在服务器上放好 /opt/jobgraph/backend/.env。

set -euo pipefail

TARGET="${1:?用法: deploy-backend.sh <user@host>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

echo "==> 检查远端环境文件"
# .env 含密钥，不随包上传，由部署者预先放置。缺失时后端可以启动但抽取必然失败，
# 故在推送之前拦下，避免把问题推迟到第一次上传简历时才暴露。
if ! ssh "$TARGET" 'test -s /opt/jobgraph/backend/.env'; then
    echo "远端缺少 /opt/jobgraph/backend/.env（或为空）。" >&2
    echo "以 deploy/env/backend.env.example 为模板在服务器上建立，填入 LLM_API_KEY" >&2
    echo "与实际域名，权限设为 600、属主 jobgraph，然后重试。" >&2
    exit 1
fi

echo "==> 打包后端"
# 排除三类内容：本地虚拟环境与缓存、含密钥或本地状态的文件、
# 以及测试与交付文档等不参与运行的部分。job_data 为接口的取数来源，必须随行。
TARBALL="$(mktemp -t jobgraph-backend-XXXXXX).tar.gz"
tar -czf "$TARBALL" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.env' \
    --exclude='runtime/uploads/*' \
    --exclude='runtime/outputs/*' \
    --exclude='tests' \
    --exclude='validation' \
    --exclude='examples' \
    --exclude='frontend' \
    --exclude='docs' \
    --exclude='coverage.xml' \
    --exclude='.coveragerc' \
    --exclude='HANDOFF_MANIFEST.json' \
    .

echo "==> 上传"
scp "$TARBALL" "${TARGET}:/tmp/jobgraph-backend.tar.gz"
rm -f "$TARBALL"

echo "==> 远端解包与重启"
ssh "$TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/jobgraph/backend

# 就地覆盖而非整目录替换：.env 与 .venv 均在此目录内，须原样保留。
tar -xzf /tmp/jobgraph-backend.tar.gz -C /opt/jobgraph/backend
rm -f /tmp/jobgraph-backend.tar.gz

if [ ! -d .venv ]; then
    echo "==> 首次部署，创建虚拟环境"
    python3 -m venv .venv
fi
./.venv/bin/pip install --upgrade pip --quiet
./.venv/bin/pip install -r requirements.txt --quiet

mkdir -p runtime/uploads runtime/outputs
chown -R jobgraph:jobgraph /opt/jobgraph/backend
chmod 600 /opt/jobgraph/backend/.env

systemctl restart jobgraph-api
sleep 3
systemctl is-active --quiet jobgraph-api && echo "服务已启动" || {
    echo "服务未能启动，最近日志："
    journalctl -u jobgraph-api -n 30 --no-pager
    exit 1
}
REMOTE

echo "==> 自检"
ssh "$TARGET" 'curl -fsS http://127.0.0.1:8000/health' && echo
echo "==> 完成"
