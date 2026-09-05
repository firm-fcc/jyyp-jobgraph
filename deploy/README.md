# 部署

面向中国大陆网络环境的公网发布方案。前端静态产物与人岗匹配服务部署于同一台
境外主机，同域交付，以单一 HTTPS 域名对外。

## 一、形态

```
                    https://<域名>
                          │
                    ┌─────┴─────┐
                    │   Nginx   │
                    └─────┬─────┘
             ┌────────────┴────────────┐
             │                         │
      /  /assets/  /data/        /api/  /health
             │                         │
     /var/www/jobgraph          127.0.0.1:8000
      （静态产物 17 MB）        （uvicorn，常驻）
                                       │
                             api.deepseek.com
```

同域交付使浏览器发出的全部请求同源，跨域与混合内容两类问题均不出现，
前端只需一个指向站点自身的 `VITE_MATCH_API`。

## 二、选型

| 项 | 取值 | 依据 |
|---|---|---|
| 地域 | 香港 | 境外节点中距大陆最近，延迟约 30–80 ms |
| 规格 | 2 核 4 GB | 常驻进程约 300 MB，每次简历抽取另派生约 200 MB 的子进程 |
| 带宽 | 峰值 30 Mbps 及以上 | 首屏传输量见下表 |
| 系统 | Ubuntu 24.04 LTS | 自带 Python 3.12，满足后端 3.11 的下界 |
| 磁盘 | 40 GB | 产物 17 MB、后端连数据 13 MB、虚拟环境约 300 MB |

首屏传输量实测：

| | 原始 | gzip 后 |
|---|---|---|
| `data/` 十二个 JSON | 15.10 MB | 2.97 MB |
| `assets/` 与入口 | 0.90 MB | 0.29 MB |
| 合计 | 16.00 MB | **3.26 MB** |

压缩比在五倍以上，故 JSON 的压缩属必须项，配置中已启用。预压缩产物的直接交付
依赖可选模块 `gzip_static`，由初始化脚本检测后自动启用，未编入时回落实时压缩，
结果相同而多耗 CPU。30 Mbps 下单次完整加载约需一秒，十人并发约需十秒；
`assets/` 长期缓存、`data/` 五分钟内复用，重复访问的传输量远低于首次。

## 三、步骤

### 0 · 前置

宿主机一侧需要 Node 20 及以上、以及 `ssh`、`scp`、`tar`、`gzip`。三个脚本均为
Bash 脚本，在 Windows 上须于 Git Bash 中执行，PowerShell 无法直接运行。
主机一侧只需一个可登录的 root 账户。

### 1 · 域名与解析

境外主机不受 ICP 备案约束，域名注册后即可解析使用。在注册商处添加一条
A 记录指向主机公网地址，TTL 取十分钟。解析生效后确认：

```bash
nslookup <域名>
```

### 2 · 主机初始化

以 root 登录主机，执行：

```bash
bash server-setup.sh <域名>
```

该脚本安装 Nginx、Python 虚拟环境组件与 certbot，建立不可登录的系统账户
`jobgraph`，创建发布目录与证书校验目录，并只放行 22、80、443 三个端口。
后端监听回环地址，8000 不对外开放。

### 3 · Nginx 与证书

证书签发要求 80 端口先能应答，故分两步。先放置过渡配置：

```bash
cp jobgraph-http.conf  /etc/nginx/conf.d/
cp jobgraph-proxy.conf   /etc/nginx/snippets/
cp jobgraph-bootstrap.conf /etc/nginx/sites-available/jobgraph.conf
sed -i 's/example\.com/<域名>/g' /etc/nginx/sites-available/jobgraph.conf
ln -sf /etc/nginx/sites-available/jobgraph.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

签发证书：

```bash
certbot certonly --webroot -w /var/www/acme -d <域名> --agree-tos -m <邮箱> -n
```

换用正式配置：

```bash
cp jobgraph.conf /etc/nginx/sites-available/jobgraph.conf
sed -i 's/example\.com/<域名>/g' /etc/nginx/sites-available/jobgraph.conf
nginx -t && systemctl reload nginx
```

certbot 随包安装续期定时器，无须另设。校验目录 `/var/www/acme` 独立于站点根，
前端发布采用整目录替换，两者同址会使续期在下一次发布后失败。

### 4 · 后端上线

先在主机上放置环境文件，其内容以 `env/backend.env.example` 为模板，
`LLM_API_KEY` 与域名两项须按实际填写：

```bash
install -o jobgraph -g jobgraph -m 600 /dev/null /opt/jobgraph/backend/.env
vim /opt/jobgraph/backend/.env
```

安装服务单元：

```bash
cp jobgraph-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable jobgraph-api
```

从宿主机推送代码并启动：

```bash
bash deploy/scripts/deploy-backend.sh root@<主机地址>
```

脚本排除本地虚拟环境、测试、验证记录与 `.env`，只上传运行所需的代码、
`job_data/` 与阈值配置，随后同步依赖并重启服务，最后请求一次 `/health` 自检。

**首次部署须验证模型可达性**，境外主机访问 `api.deepseek.com` 的连通性
不应默认成立：

```bash
ssh root@<主机地址> 'curl -sS -o /dev/null -w "%{http_code}\n" \
    -X POST https://api.deepseek.com/chat/completions \
    -H "Content-Type: application/json" -H "Authorization: Bearer $(grep ^LLM_API_KEY= /opt/jobgraph/backend/.env | cut -d= -f2)" \
    -d "{\"model\":\"deepseek-v4-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}"'
```

返回 200 即通。返回 401 说明密钥有误，连接超时说明该地域无法直连，
此时须改用可直连的地域或另配出口。

### 5 · 前端上线

在仓库根建立 `frontend/.env.production`，以 `env/frontend.env.production.example`
为模板，填入站点自身的 HTTPS 地址。该取值在构建时写死进产物，域名变更后
必须重新构建。随后：

```bash
bash deploy/scripts/deploy-frontend.sh root@<主机地址>
```

脚本构建产物、剔除原型页、逐文件预压缩，再以新旧目录整体切换的方式上线，
中途不存在半份产物对外可见的窗口。上一版保留于 `/var/www/jobgraph.old`。

## 四、验收

```bash
# 证书与跳转
curl -sSI http://<域名>/ | head -1                    # 期望 301
curl -sSI https://<域名>/ | head -1                    # 期望 200

# 压缩生效
curl -sSI -H 'Accept-Encoding: gzip' https://<域名>/data/graph.json \
    | grep -i content-encoding                         # 期望 gzip

# 后端连通与模型配置
curl -sS https://<域名>/health                         # 期望 llm_configured 为 true
```

界面一侧逐项确认：封面四项指标非零、全景图谱与岗位洞察正常渲染、
人岗匹配页顶部显示实测链路而非演示链路、上传一份简历可跑完整条抽取。

## 五、日常操作

| 场景 | 命令 |
|---|---|
| 数据换版后重新发布 | `bash deploy/scripts/deploy-frontend.sh root@<主机>` |
| 后端代码更新 | `bash deploy/scripts/deploy-backend.sh root@<主机>` |
| 查看服务日志 | `journalctl -u jobgraph-api -f` |
| 查看抽取耗时 | `ls -t /opt/jobgraph/backend/runtime/outputs/*.timing.jsonl \| head` |
| 回退前端 | 对调 `/var/www/jobgraph` 与 `/var/www/jobgraph.old` 后 reload |
| 手动续期证书 | `certbot renew --webroot -w /var/www/acme` |

## 六、已知约束

**境外节点的稳定性有上界。** 香港节点在大陆可正常访问，但国际出口在晚间高峰
存在拥塞与丢包，各运营商表现不一。这是境外部署的固有限制，只能由境内节点消除，
而境内节点须先完成 ICP 备案。

**长请求可能被链路中途切断，这是本方案最脆弱的一环。** 简历抽取由后端派生
子进程同步执行，单次耗时以分钟计，期间连接无任何数据往返。移动网络与办公网络的
NAT 多在五分钟左右回收空闲映射，跨境链路途经的中间设备更多，回收概率高于境内。
配置已在 listen 上开启 so_keepalive，使内核在空闲期持续发出探测包以维持映射，
可覆盖其中的多数情形。

残余风险仍在：探测包只能维持途中设备的映射，无法阻止客户端一侧主动断开，
诸如切换网络、移动端浏览器在后台被系统回收、笔记本休眠。彻底的解法是把该接口
改为任务提交与轮询两段式，使单次连接的存续时间从数分钟降至一秒以内，
属代码改动，未包含在本方案内。在此之前，演示时宜提示上传后勿切走页面。

**并发抽取的上限由模型服务决定。** 单次抽取内部即以六路并发调用模型，
若干人同时上传会使并发请求数成倍上升，先触及的是模型服务一侧的速率限制而非
服务器资源。现场演示宜逐人依次上传，不宜鼓励同时提交。

**接口无鉴权。** `/api/candidate` 每次调用均消耗模型额度，公网暴露后存在被反复
调用的可能。现由 Nginx 施加速率与并发两道限额（每来源每分钟 20 次、并发 6）。
两项均按来源地址计，而校园网与会场网络的公网出口多为 NAT，全部观众共用一个
地址，故取值已偏宽；额度敏感时应另加访问令牌，仅靠按地址限流并不足以防刷。

**简历属于个人信息。** 上传文件在处理结束后即时删除，抽取结果不落盘留存，
但公开演示时仍应就数据用途作出说明。
