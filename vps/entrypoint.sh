#!/bin/sh
set -e

echo "=== [edgetunnel VPS] Starting Initialization ==="

DATA_DIR=${DATA_DIR:-/app/data}
mkdir -p "$DATA_DIR"

# 1. 触发 Python 首次加载与 Reality 密钥生成
python3 -c "
import sys, os
sys.path.insert(0, '/app/vps')
import app
cfg = app.load_config()
print(f'[*] Loaded UUID: {cfg[\"uuid\"]}')
print(f'[*] Reality PubKey: {cfg[\"reality_public_key\"]}')
print(f'[*] Sub Token: {cfg[\"sub_token\"]}')
"

# 2. 检查/注册 Cloudflare WARP 账户凭据
python3 -c "
import sys, os
sys.path.insert(0, '/app/vps')
import warp
warp_info = warp.get_or_register_warp('$DATA_DIR')
print(f'[*] Cloudflare WARP Active: IPv4={warp_info[\"v4_addr\"]}')
"

# 3. 读取配置并合成实际运行的 xray.json
python3 -c "
import json, os
cfg = json.load(open('$DATA_DIR/config.json'))
warp_cfg = json.load(open('$DATA_DIR/warp.json'))
tpl = open('/app/vps/xray_config.json').read()

tpl = tpl.replace('%%UUID%%', cfg['uuid'])
tpl = tpl.replace('%%REALITY_PRIVATE_KEY%%', cfg.get('reality_private_key', ''))
tpl = tpl.replace('%%REALITY_SHORT_ID%%', cfg.get('reality_short_id', '0123456789abcdef'))

tpl = tpl.replace('%%WARP_SECRET_KEY%%', warp_cfg['secret_key'])
tpl = tpl.replace('%%WARP_V4%%', warp_cfg['v4_addr'])
tpl = tpl.replace('%%WARP_V6%%', warp_cfg['v6_addr'])
tpl = tpl.replace('%%WARP_PEER_PUB%%', warp_cfg['peer_pub'])
tpl = tpl.replace('%%WARP_ENDPOINT%%', warp_cfg['endpoint'])

with open('/tmp/xray.json', 'w') as f:
    f.write(tpl)
print('[*] Generated /tmp/xray.json with Global Cloudflare WARP Outbound successfully')
"

# 4. 启动 Xray 核心
if command -v xray >/dev/null 2>&1; then
    echo "[*] Launching Xray-core..."
    xray run -c /tmp/xray.json &
    XRAY_PID=$!
    echo "[*] Xray-core running (PID: $XRAY_PID)"
else
    echo "[Warn] xray binary not found in PATH, skipping xray process"
fi

# 5. 启动 Web 服务
echo "[*] Launching Web Management Service..."
exec python3 /app/vps/app.py
