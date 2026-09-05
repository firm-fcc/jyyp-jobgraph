#!/usr/bin/env bash
#
# 前端发布：本地构建、预压缩、整目录替换上线。在仓库根的宿主机执行。
#
#   bash deploy/scripts/deploy-frontend.sh root@1.2.3.4
#
# 发布采用新旧目录整体切换，中途不存在半份产物对外可见的窗口。
# 上一版保留于 /var/www/jobgraph.old，回退时对调两个目录即可。

set -euo pipefail

TARGET="${1:?用法: deploy-frontend.sh <user@host>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# VITE_MATCH_API 在构建期写死进产物。该项缺失或仍为样例取值时，构建出的产物
# 中人岗匹配页会静默回落演示链路，上线后无从由服务器一侧补救，故构建前先核对。
ENV_FILE="frontend/.env.production"
if [ ! -f "$ENV_FILE" ]; then
    echo "缺少 ${ENV_FILE}。以 deploy/env/frontend.env.production.example 为模板建立，" >&2
    echo "填入站点自身的 https 地址后重试。" >&2
    exit 1
fi
if grep -q 'example\.com' "$ENV_FILE"; then
    echo "${ENV_FILE} 中仍是样例域名 example.com，请改为实际域名。" >&2
    exit 1
fi
echo "==> 构建前端（VITE_MATCH_API = $(grep '^VITE_MATCH_API=' "$ENV_FILE" | cut -d= -f2-)）"
npm run build

echo "==> 剔除非发布内容"
rm -f dist/_proto.html

echo "==> 预压缩，配合 Nginx 的 gzip_static"
# 保留原文件：不支持 gzip 的客户端仍需回落到未压缩版本。
find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.json' \
    -o -name '*.html' -o -name '*.svg' \) -print0 \
    | xargs -0 -n1 gzip -9 --keep --force

echo "==> 打包上传"
TARBALL="$(mktemp -t jobgraph-dist-XXXXXX).tar.gz"
tar -czf "$TARBALL" -C dist .
scp "$TARBALL" "${TARGET}:/tmp/jobgraph-dist.tar.gz"
rm -f "$TARBALL"

echo "==> 远端切换"
ssh "$TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
rm -rf /var/www/jobgraph.new
mkdir -p /var/www/jobgraph.new
tar -xzf /tmp/jobgraph-dist.tar.gz -C /var/www/jobgraph.new
chown -R www-data:www-data /var/www/jobgraph.new

rm -rf /var/www/jobgraph.old
if [ -d /var/www/jobgraph ]; then
    mv /var/www/jobgraph /var/www/jobgraph.old
fi
mv /var/www/jobgraph.new /var/www/jobgraph
rm -f /tmp/jobgraph-dist.tar.gz
echo "已切换至新版本，上一版保留于 /var/www/jobgraph.old"
REMOTE

echo "==> 完成"
