# Multi-stage lightweight build for edgetunnel VPS
FROM python:3.11-alpine

LABEL maintainer="FountainChan <https://github.com/Fount/edgetunnel>"
LABEL description="edgetunnel VPS Edition - Standalone Airport & Admin Service"

ARG XRAY_VERSION=v1.8.24
ARG TARGETARCH

ENV DATA_DIR=/app/data \
    PORT=8787 \
    TZ=Asia/Shanghai

WORKDIR /app

# 安装运行基础依赖
RUN apk add --no-cache ca-certificates tzdata curl unzip

# 下载安装 Xray-core
RUN set -ex && \
    ARCH="${TARGETARCH:-amd64}" && \
    case "$ARCH" in \
      "amd64"|"x86_64") XRAY_ARCH="64" ;; \
      "arm64"|"aarch64") XRAY_ARCH="arm64-v8a" ;; \
      *) XRAY_ARCH="64" ;; \
    esac && \
    echo "Downloading Xray-core for arch: ${XRAY_ARCH}..." && \
    curl -sSL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip" && \
    mkdir -p /usr/local/bin /usr/local/share/xray && \
    unzip -q /tmp/xray.zip -d /tmp/xray && \
    mv /tmp/xray/xray /usr/local/bin/xray && \
    mv /tmp/xray/geoip.dat /usr/local/share/xray/ 2>/dev/null || true && \
    mv /tmp/xray/geosite.dat /usr/local/share/xray/ 2>/dev/null || true && \
    chmod +x /usr/local/bin/xray && \
    rm -rf /tmp/xray*

# 拷贝服务端工程代码
COPY vps /app/vps

RUN chmod +x /app/vps/entrypoint.sh

# 暴露 Web 管理端口 (8787) 与 Reality 端口 (9443)
EXPOSE 8787 9443

VOLUME ["/app/data"]

ENTRYPOINT ["/bin/sh", "/app/vps/entrypoint.sh"]
