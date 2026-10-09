# edgetunnel VPS 独立自建机场与 GitHub Actions 自动化部署方案

> **项目定位**：在现有 `Fount/edgetunnel` 仓库内，拓展一套运行于独立 VPS（`64.112.40.121`）上的高性能、无额度限制、自带 `/admin` 优选 IP 管理界面的独立自建机场系统，并通过 GitHub Actions 实现全自动构建与热更新部署。

---

## 目录
1. [一、项目背景与核心战略](#一项目背景与核心战略)
2. [二、系统整体架构与流量流向](#二系统整体架构与流量流向)
3. [三、Cloudflare 域名与 DNS 网络规划](#三cloudflare-域名与-dns-网络规划)
4. [四、VPS 宿主机环境与端口分流设计](#四vps-宿主机环境与端口分流设计)
5. [五、VPS 独立机场核心模块设计](#五vps-独立机场核心模块设计)
   - 5.1 [目录结构设计](#51-代码与目录结构)
   - 5.2 [Web 管理后台（/admin）](#52-web-管理后台-admin)
   - 5.3 [订阅生成引擎（/sub）](#53-订阅生成引擎-sub)
   - 5.4 [底层代理核心（Xray-core）配置](#54-底层代理核心xray-core配置)
   - 5.5 [数据持久化安全设计](#55-数据持久化安全设计)
6. [六、GitHub Actions 自动化 CI/CD 流水线](#六github-actions-自动化-cicd-流水线)
7. [七、与家庭网络（fe-nas / Clash Verge）联动整合](#七与家庭网络fe-nas--clash-verge联动整合)
8. [八、分步实施路线图与操作指南](#八分步实施路线图与操作指南)

---

## 一、项目背景与核心战略

### 1.1 现状与痛点
- **Cloudflare Pages/Worker 免费额度瓶颈**：原版 `edgetunnel` 运行在 Cloudflare Serverless 虚拟机中，每日免费额度为 10 万次请求。由于客户端（Clash Verge、多端同步）定时测速与大量节点高频探测，极易在单日内烧穿配额导致全网节点同时瘫痪。
- **ProxyIP 中转开销与死锁**：Worker 平台受限于自连死锁限制，访问托管在 Cloudflare 上的站点（如 OpenAI、Canva）必须强依赖第三方反代池（`cmliussss.net` 等），存在二次中转延迟和反代池失效风险。
- **节点数量与稳定性的矛盾**：为了保障网络可用性，需要维护 80~100+ 个优选 IP；但节点越多，CF Worker 额度消耗越快。

### 1.2 核心双轨战略
将机场系统解耦为两套职责分明的独立体系：

| 系统 | 运行载体 | 节点规模 | 战略定位 | 额度限制 |
|---|---|---|---|---|
| **原版 Cloudflare (`ffly.ccwu.cc`)** | Cloudflare Pages / Worker | **精选 3~5 个极稳节点** | **轻量备用容灾**（日常静默，日调用量 < 5000 次） | 受 10 万次/日限制 |
| **新增 VPS 机场 (`node.ffly.ccwu.cc`)** | 独立 VPS（`64.112.40.121`） | **海量 80~100+ 优选节点 + 1 个 Reality 直连** | **主力全功能机场**（承接日常大流量、高频并发测速、看视频） | **0 限制，无限流量** |

---

## 二、系统整体架构与流量流向

```text
                                            【客户端（Clash Verge / 手机）】
                                                           │
                            ┌──────────────────────────────┴──────────────────────────────┐
                            ▼                                                             ▼
                【节点形态 A：VLESS-Reality 直连】                             【节点形态 B：VLESS-WS-TLS 优选节点池】
                （数量：1 个极速节点）                                          （数量：80~100+ 个优选 Anycast 入口）
                            │                                                             │
                            │ 直连 VPS 公网 IP (64.112.40.121:8443)                      │ 连接各地区 Cloudflare 优选 Anycast IP
                            ▼                                                             ▼
                ┌────────────────────────┐                                    ┌────────────────────────┐
                │   VPS 独立 Reality 端口 │                                    │  Cloudflare 边缘 CDN    │
                │      (8443/TCP)        │                                    │  (Anycast 骨干网加速)   │
                └───────────┬────────────┘                                    └───────────┬────────────┘
                            │                                                             │ 橙云回源 (SNI: node.ffly.ccwu.cc)
                            │                                                             ▼
                            │                                                 ┌────────────────────────┐
                            │                                                 │   VPS 宿主机 Nginx 443  │
                            │                                                 └───────────┬────────────┘
                            │                                                             │ 反代 WebSocket 路径 (/api-stream)
                            │                                                             ▼
                            │                                                 ┌────────────────────────┐
                            └───────────────────────┬─────────────────────────►│   Docker 内部 Xray 核心 │
                                                    │                         │      (127.0.0.1:10000) │
                                                    │                         └───────────┬────────────┘
                                                    │                                     │
                                                    ▼                                     ▼
                                      ┌────────────────────────────────────────────────────────┐
                                      │              VPS 原生 Linux TCP 出站网络                │
                                      │   (真实直连访问 Google, YouTube, OpenAI, 无 ProxyIP)    │
                                      └────────────────────────────────────────────────────────┘
```

---

## 三、Cloudflare 域名与 DNS 网络规划

### 3.1 资产与 Zone 确认
- **托管 Zone**：`ffly.ccwu.cc`（Zone ID：`f929fe569cfde711edb5e083f395678e`）；
- **新增子域名**：`node.ffly.ccwu.cc`。

### 3.2 DNS 记录配置规范
- **记录类型**：`A` 记录
- **名称 (Name)**：`node`（完整 FQDN 为 `node.ffly.ccwu.cc`）
- **IPv4 地址**：`64.112.40.121`
- **代理状态 (Proxy status)**：**已代理（开启橙云 Proxied）**
- **TTL**：Auto

### 3.3 SSL/TLS 证书机制
- **边缘侧 (Edge)**：Cloudflare Universal SSL 通配符证书自动覆盖 `*.ffly.ccwu.cc`，客户端到 CF 边缘全程自动化安全 HTTPS；
- **回源侧 (Origin)**：
  - Cloudflare SSL 模式设为 **Full** 或 **Strict**；
  - VPS 宿主机 Nginx 使用 Cloudflare 免费颁发的 **Origin CA 15 年长效证书**，彻底免除 Let's Encrypt 每 90 天自动续期失败的风险。

---

## 四、VPS 宿主机环境与端口分流设计

### 4.1 VPS 基础设施规格
- **主机名/IP**：`moneylab.work` / `64.112.40.121`（Ubuntu 22.04 LTS，2 核 CPU / 2GB 内存 / 36GB 可用空间）；
- **已运行服务**：Docker 容器 `vast-vast-1`（影视仓，绑定内网 `127.0.0.1:18788`），不冲突。

### 4.2 端口与流量拓扑划分

```text
【公网流量】
   │
   ├─► 80/TCP   ──► 宿主机 Nginx ──► HTTP 跳转 HTTPS (或处理 ACME 校验)
   │
   ├─► 443/TCP  ──► 宿主机 Nginx (SSL 终结与 SNI 分流)
   │                  ├─► server_name tv.379268.xyz   ──► 127.0.0.1:18788 (vast 影视仓)
   │                  └─► server_name node.ffly.ccwu.cc
   │                        ├─► location /admin ─────► 127.0.0.1:8787 (Web 后台管理)
   │                        ├─► location /sub   ─────► 127.0.0.1:8787 (订阅生成接口)
   │                        └─► location /api-stream ─► 127.0.0.1:10000 (Xray WebSocket)
   │
   └─► 8443/TCP ──► Docker Xray 容器直接监听 ──► VLESS-Reality 原生极速直连入站
```

### 4.3 防火墙 (UFW) 安全加固规则
- **22/TCP**：开放公网（SSH 管理）；
- **80/TCP**：开放公网；
- **443/TCP**：保持现有策略——**仅允许 Cloudflare 官方 IP 段接入**（彻底隐藏源站真实 Web 服务，防探测）；
- **8443/TCP**：**开放公网**（允许 Reality 客户端直接与 VPS 进行 TLS 握手）。

---

## 五、VPS 独立机场核心模块设计

### 5.1 代码与目录结构（在 `Fount/edgetunnel` 仓库内）

```text
Fount/edgetunnel/
├── _worker.js                         # 原版 Cloudflare Worker 脚本
├── wrangler.toml                      # Pages 配置文件
│
├── docs/                              # 【新增】工程设计与部署文档
│   └── vps-deployment-design.md       # 本设计方案文件
│
├── vps/                               # 【新增】VPS 服务端工程
│   ├── app.py                         # 核心 Web 服务（提供 /admin 与 /sub）
│   ├── config.example.json            # 基础配置模板（UUID、密码、端口、Token）
│   ├── xray_config.json               # Xray-core 核心路由与入站模版
│   ├── templates/
│   │   ├── login.html                 # 原版风格管理员登录页
│   │   └── admin.html                 # 原版风格大文本框管理主页 (ADD.txt / ADDAPI)
│   ├── static/                        # 静态资源（CSS / 图标）
│   └── entrypoint.sh                  # 容器启动脚本（同时拉起 Xray 与 Web 服务）
│
├── Dockerfile                         # 多阶段轻量化镜像构建文件
├── docker-compose.yml                 # 生产容器编排文件
│
└── .github/workflows/
    ├── sync.yml                       # 现有工作流
    └── deploy-vps.yml                 # 【新增】VPS 自动构建部署流水线
```

---

### 5.2 Web 管理后台（/admin）

1. **认证机制**：
   - 访问 `https://node.ffly.ccwu.cc/admin`；
   - 支持设置独立 `ADMIN_PASSWORD`；登录成功后下发加密 Cookie（`HttpOnly; SameSite=Lax`），过期时间 7 天。
2. **页面交互（高度还原原版体验）**：
   - **自定义优选 IP / 域名大文本框（`ADD.txt`）**：
     - 单行格式支持：`IP:端口#备注`、`域名:端口#备注` 或纯 `IP`（自动补全 443）；
     - 支持直接全选粘贴 80~100+ 个优选 IP。
   - **远程优选 API（`ADDAPI`）**：
     - 支持填入自动测速 API 链接（如 `https://.../bestip.txt`），每次请求订阅时动态合并。
   - **基础配置卡片**：
     - 显示当前节点使用的 `UUID`、`WS Path (/api-stream)`、`Reality 公钥 (PublicKey)`。
   - **操作栏**：
     - `[ 💾 保存配置 ]`（即时写入持久化存储，无需重启容器）；
     - `[ 📋 复制 Clash 订阅地址 ]`、`[ 📋 复制通用 VLESS 链接 ]`。

---

### 5.3 订阅生成引擎（/sub）

访问 `https://node.ffly.ccwu.cc/sub?token=YOUR_TOKEN` 时，服务端执行以下生成逻辑：

1. **身份鉴权**：校验 URL 中的 `token` 参数是否与配置文件一致；
2. **节点聚合构建**：
   - **节点 1（Reality 直连）**：
     ```yaml
     - name: "🚀 VPS-Reality极速直连"
       type: vless
       server: 64.112.40.121
       port: 8443
       uuid: <UUID>
       network: tcp
       tls: true
       udp: true
       flow: xtls-rprx-vision
       servername: gateway.icloud.com
       reality-opts:
         public-key: <REALITY_PUBLIC_KEY>
         short-id: <REALITY_SHORT_ID>
       client-fingerprint: chrome
     ```
   - **节点 2 ~ N（CF 优选节点池）**：
     - 遍历 `ADD.txt` 中的每一行（如 `104.16.1.1:443#移动优选01`）；
     - 生成指向 `node.ffly.ccwu.cc` 的标准 VLESS-WS-TLS 节点：
     ```yaml
     - name: "☁️ 移动优选01"
       type: vless
       server: 104.16.1.1
       port: 443
       uuid: <UUID>
       tls: true
       udp: true
       network: ws
       servername: node.ffly.ccwu.cc
       ws-opts:
         path: /api-stream
         headers:
           Host: node.ffly.ccwu.cc
       client-fingerprint: chrome
     ```
3. **策略组（Proxy Groups）自动装配**：
   - **`🚀 节点选择 (select)`**：主入口，包含 `♻️ 自动选择`、`🔯 故障转移`、`🚀 VPS-Reality极速直连` 以及全部优选节点；
   - **`♻️ 自动选择 (url-test)`**：`interval: 600`、`lazy: true`、`tolerance: 100`，全自动探测最低延迟入口；
   - **`🔯 故障转移 (fallback)`**：`interval: 600`、`lazy: true`；
   - **分流规则**：集成标准的国内域名/IP 直连（`GEOIP,CN,DIRECT`）与广告拦截规则。

---

### 5.4 底层代理核心（Xray-core）配置

在容器内以服务形式常驻 `xray` 进程，配置双入站：

```json
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "vless-ws-in",
      "port": 10000,
      "listen": "127.0.0.1",
      "protocol": "vless",
      "settings": {
        "clients": [{ "id": "YOUR_UUID" }],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "ws",
        "wsSettings": { "path": "/api-stream" }
      }
    },
    {
      "tag": "vless-reality-in",
      "port": 8443,
      "listen": "0.0.0.0",
      "protocol": "vless",
      "settings": {
        "clients": [{ "id": "YOUR_UUID", "flow": "xtls-rprx-vision" }],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "gateway.icloud.com:443",
          "xver": 0,
          "serverNames": ["gateway.icloud.com"],
          "privateKey": "YOUR_REALITY_PRIVATE_KEY",
          "shortIds": ["0123456789abcdef"]
        }
      }
    }
  ],
  "outbounds": [
    { "protocol": "freedom", "tag": "direct" },
    { "protocol": "blackhole", "tag": "block" }
  ]
}
```

---

### 5.5 数据持久化安全设计

- **宿主机持久化目录**：`/opt/vps-sub/data`
- **挂载关系**：
  ```yaml
  volumes:
    - /opt/vps-sub/data:/app/data
  ```
- **存储内容**：
  - `data/config.json`：存储管理员密码哈希、订阅 Token、UUID、Reality 私钥；
  - `data/ADD.txt`：存储你在 `/admin` 页面保存的海量优选 IP 列表；
  - `data/ADDAPI.txt`：存储远程优选 API 地址。
- **效果**：无论 Docker 镜像如何重新构建、拉取、升级或重启，所有节点数据与配置持久锁定在宿主机硬盘，**零丢失风险**。

---

## 六、GitHub Actions 自动化 CI/CD 流水线

### 6.1 工作流触发逻辑（`.github/workflows/deploy-vps.yml`）
- **自动触发**：当 `main` 分支上的 `vps/**`、`Dockerfile` 或 `docker-compose.yml` 发生变更提交时；
- **手动触发**：在 GitHub 仓库页面点击 **Actions $\to$ Deploy VPS Sub Service $\to$ Run workflow**。

### 6.2 流水线配置完整定义

```yaml
name: Deploy VPS Sub Service

on:
  push:
    branches: [ main ]
    paths:
      - 'vps/**'
      - 'Dockerfile'
      - 'docker-compose.yml'
      - '.github/workflows/deploy-vps.yml'
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}-vps

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 登录 GitHub Container Registry (GHCR)
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: 设置 Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: 构建并推送 Docker 镜像
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./Dockerfile
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: SSH 远程部署到 VPS
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          port: 22
          script: |
            set -e
            echo "==> 登录 GHCR"
            echo "${{ secrets.GITHUB_TOKEN }}" | sudo docker login ghcr.io -u ${{ github.actor }} --password-stdin
            
            echo "==> 准备运行目录与配置"
            sudo mkdir -p /opt/vps-sub/data
            sudo chown -R installer:installer /opt/vps-sub
            
            cd /opt/vps-sub
            # 下载仓库中的最新 docker-compose.yml
            cat << 'EOF' > docker-compose.yml
            version: '3.8'
            services:
              vps-sub:
                image: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
                container_name: vps-sub-service
                restart: always
                network_mode: host
                volumes:
                  - /opt/vps-sub/data:/app/data
                environment:
                  - TZ=Asia/Shanghai
            EOF
            
            echo "==> 拉取最新镜像并重启容器"
            sudo docker compose pull
            sudo docker compose up -d --remove-orphans
            sudo docker image prune -f
            echo "==> 部署完成，服务已正常启动！"
```

### 6.3 GitHub Secrets 清单
在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 中配置以下 3 个变量：

| Secret 名称 | 说明 | 示例值 |
|---|---|---|
| `VPS_HOST` | VPS 公网 IPv4 地址 | `64.112.40.121` |
| `VPS_USER` | SSH 登录用户名 | `installer` |
| `VPS_SSH_KEY` | 免密登录私钥文本 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |

---

## 七、与家庭网络（fe-nas / Clash Verge）联动整合

部署完成后，你的家庭网络与多端设备将形成清晰的三级机场管理体系：

```text
                               ┌──────────────────────────┐
                               │       🎯 选择机场         │
                               └────────────┬─────────────┘
                                            │
        ┌───────────────────────────────────┼───────────────────────────────────┐
        ▼                                   ▼                                   ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐  ┌──────────────────────────────┐
│  🛫 机场 1：自建 VPS (新增)   │  │  ☁️ 机场 2：Cloudflare (原版)│  │  🌐 机场 3：大哥云 (商业备用) │
├──────────────────────────────┤  ├──────────────────────────────┤  ├──────────────────────────────┤
│ ├─ 订阅: node.ffly.ccwu.cc   │  │ ├─ 订阅: ffly.ccwu.cc        │  │ ├─ 订阅: 大哥云 Provider     │
│ ├─ 80+ 个海量优选 IP 节点    │  │ ├─ 3~5 个精选稳定节点        │  │ └─ 11 个全球地区故障转移组   │
│ ├─ 1 个 Reality 极速直连节点 │  │ └─ 零额度压力，充当备用      │  │                              │
│ └─ 主力承载大流量与测速      │  │                              │  │                              │
└──────────────────────────────┘  └──────────────────────────────┘  └──────────────────────────────┘
```

1. **PC / 手机端**：直接在 Clash Verge 中添加订阅 `https://node.ffly.ccwu.cc/sub?token=xxx` 作为主力机场；
2. **NAS 聚合端（fe-nas）**：在 `ffly-sub-service` 中将这套自建 VPS 订阅挂载为独立 Provider，无缝融入 NAS 主配置的 `选择机场` 顶级组中。

---

## 八、分步实施路线图与操作指南

### 阶段一：Cloudflare 域名解析（前置）
1. 在 Cloudflare 控制台中进入 `ffly.ccwu.cc` Zone；
2. 添加 A 记录：`node` $\to$ `64.112.40.121`，开启小橙云（Proxied）。

### 阶段二：代码开发与仓库提交
1. 在本地 `D:\WorkDev\MyShare\edgetunnel` 仓库中：
   - 编写 `vps/` 目录下的 Web 服务与 Xray 模板；
   - 编写 `Dockerfile` 与 `docker-compose.yml`；
   - 编写 `.github/workflows/deploy-vps.yml` 流水线文件。
2. 提交代码并推送至 `git@github.com:Fount/edgetunnel.git`。

### 阶段三：VPS 宿主机 Nginx 与防火墙准备
1. 在 VPS（`64.112.40.121`）上配置 Nginx 站点，配置 `/etc/nginx/sites-available/node.ffly.ccwu.cc`：
   - 监听 443 端口并配置 Cloudflare Origin 证书；
   - 配置 `/admin`、`/sub`、`/api-stream` 反代规则。
2. UFW 防火墙放行 `8443/tcp`。

### 阶段四：GitHub Actions 部署与验收
1. 在 GitHub 仓库中配置 `VPS_HOST`、`VPS_USER`、`VPS_SSH_KEY` 三个 Secrets；
2. 触发 GitHub Action，等待流水线构建完成并自动部署至 VPS；
3. 浏览器打开 `https://node.ffly.ccwu.cc/admin`，登录后台并粘贴 80+ 个优选 IP；
4. 复制生成的 `/sub` 订阅链接，导入 Clash Verge 验证全节点连通性与 Reality 直连延迟。
