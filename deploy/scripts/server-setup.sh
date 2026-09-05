#!/usr/bin/env bash
#
# 服务器初始化，在全新的 Ubuntu 24.04 实例上以 root 执行一次。
# Ubuntu 24.04 自带 Python 3.12，满足后端 3.11 及以上的下界，无须另装解释器。
#
#   bash server-setup.sh example.com
#
# 执行完毕后服务器具备：Nginx、Python 虚拟环境所需组件、certbot、
# 专用系统用户 jobgraph、发布目录与校验目录。站点配置与代码仍需另行上传。

set -euo pipefail

DOMAIN="${1:?用法: server-setup.sh <域名>}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx python3-venv python3-pip certbot rsync ufw

# 服务以专用账户运行，不授予登录权限。
if ! id jobgraph >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /opt/jobgraph --shell /usr/sbin/nologin jobgraph
fi

install -d -o www-data -g www-data /var/www/jobgraph
install -d -o www-data -g www-data /var/www/acme
install -d -o jobgraph -g jobgraph /opt/jobgraph/backend

# 预压缩产物的直接交付依赖 ngx_http_gzip_static_module，该模块是否编入随发行版
# 而异。检出后以 http 级配置启用，各 server 继承；未编入时保持实时压缩，
# 结果相同，仅多耗 CPU。
if nginx -V 2>&1 | grep -q -- '--with-http_gzip_static_module'; then
    cat > /etc/nginx/conf.d/jobgraph-gzip-static.conf <<'GZIPSTATIC'
# 由 server-setup.sh 写入：本机 Nginx 已编入 gzip_static 模块。
gzip_static on;
GZIPSTATIC
    echo "已启用 gzip_static（预压缩产物直发）"
else
    echo "本机 Nginx 未编入 gzip_static 模块，将使用实时压缩"
fi

# 仅放行 SSH 与 HTTP(S)。后端监听回环地址，8000 不对外开放。
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo
echo "初始化完成。后续步骤："
echo "  1. 把 deploy/nginx/jobgraph-http.conf 放入 /etc/nginx/conf.d/"
echo "  2. 把 deploy/nginx/jobgraph-proxy.conf 放入 /etc/nginx/snippets/"
echo "  3. 把 deploy/nginx/jobgraph-bootstrap.conf 放入 /etc/nginx/sites-available/jobgraph.conf"
echo "     并把其中的 example.com 全部替换为 ${DOMAIN}"
echo "  4. ln -sf /etc/nginx/sites-available/jobgraph.conf /etc/nginx/sites-enabled/"
echo "     rm -f /etc/nginx/sites-enabled/default && nginx -t && systemctl reload nginx"
echo "  5. certbot certonly --webroot -w /var/www/acme -d ${DOMAIN} --agree-tos -m <邮箱> -n"
echo "  6. 用 jobgraph.conf 覆盖 jobgraph-bootstrap.conf，同样替换域名后 reload"
