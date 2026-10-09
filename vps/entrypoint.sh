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

# 2. 读取配置并合成实际运行的 xray.json
python3 -c "
import json, os
cfg = json.load(open('$DATA_DIR/config.json'))
tpl = open('/app/vps/xray_config.json').read()

tpl = tpl.replace('%%UUID%%', cfg['uuid'])
tpl = tpl.replace('%%REALITY_PRIVATE_KEY%%', cfg.get('reality_private_key', ''))
tpl = tpl.replace('%%REALITY_SHORT_ID%%', cfg.get('reality_short_id', '0123456789abcdef'))

with open('/tmp/xray.json', 'w') as f:
    f.write(tpl)
print('[*] Generated /tmp/xray.json successfully')
"

# 3. 启动 Xray 核心
if command -v xray >/dev/null 2>&1; then
    echo "[*] Launching Xray-core..."
    xray run -c /tmp/xray.json &
    XRAY_PID=$!
    echo "[*] Xray-core running (PID: $XRAY_PID)"
else
    echo "[Warn] xray binary not found in PATH, skipping xray process"
fi

# 4. 启动 Web 服务
echo "[*] Launching Web Management Service..."
exec python3 /app/vps/app.py
