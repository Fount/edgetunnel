#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloudflare WARP WireGuard 自动注册与配置管理模块"""

import json
import os
import subprocess
import ssl
import urllib.request
import time

def get_or_register_warp(data_dir):
    warp_file = os.path.join(data_dir, "warp.json")
    if os.path.exists(warp_file):
        try:
            with open(warp_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("secret_key") and data.get("v4_addr"):
                    print("[*] Loaded existing WARP account from warp.json")
                    return data
        except Exception as e:
            print(f"[Warn] Read warp.json error: {e}")

    print("[*] Registering new free Cloudflare WARP account...")
    priv_key, pub_key = "", ""
    try:
        res = subprocess.run(["xray", "x25519"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            for l in res.stdout.strip().splitlines():
                if "Private key:" in l: priv_key = l.split(":", 1)[1].strip()
                elif "Public key:" in l: pub_key = l.split(":", 1)[1].strip()
    except Exception:
        pass

    if not priv_key or not pub_key:
        priv_key = "6D3haQiiVmGTJ/ETv0RymmDqfWNISXH/Vtqhj1K+1RY="
        pub_key = "cj3b1iwK2+sQq9pKEETH1wvsg8M0tJjAFtOU0feQB04="

    std_pub = pub_key.replace("-", "+").replace("_", "/")
    while len(std_pub) % 4 != 0: std_pub += "="
    std_priv = priv_key.replace("-", "+").replace("_", "/")
    while len(std_priv) % 4 != 0: std_priv += "="

    url = "https://api.cloudflareclient.com/v0a2158/reg"
    payload = json.dumps({
        "key": std_pub,
        "install_id": "",
        "fcm_token": "",
        "tos": time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "model": "PC",
        "type": "Android",
        "locale": "zh_CN"
    }).encode("utf-8")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "okhttp/3.12.1"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            reg_data = json.load(resp)
            v4_addr = reg_data["config"]["interface"]["addresses"]["v4"]
            v6_addr = reg_data["config"]["interface"]["addresses"]["v6"]
            cf_peer_pub = reg_data["config"]["peers"][0]["public_key"]
            endpoint = "162.159.192.1:2408"
            warp_info = {
                "secret_key": std_priv,
                "v4_addr": f"{v4_addr}/32",
                "v6_addr": f"{v6_addr}/128",
                "peer_pub": cf_peer_pub,
                "endpoint": endpoint
            }
            with open(warp_file, "w", encoding="utf-8") as f:
                json.dump(warp_info, f, indent=2)
            print("[*] Successfully registered WARP account and saved to warp.json")
            return warp_info
    except Exception as e:
        print(f"[Warn] WARP online registration failed: {e}, using fallback credentials")
        warp_info = {
            "secret_key": std_priv,
            "v4_addr": "172.16.0.2/32",
            "v6_addr": "2606:4700:110:88d9:3d63:40b9:e305:7665/128",
            "peer_pub": "bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=",
            "endpoint": "162.159.192.1:2408"
        }
        with open(warp_file, "w", encoding="utf-8") as f:
            json.dump(warp_info, f, indent=2)
        return warp_info

if __name__ == "__main__":
    d = os.environ.get("DATA_DIR", "/app/data")
    os.makedirs(d, exist_ok=True)
    res = get_or_register_warp(d)
    print("WARP Account Ready:", res["v4_addr"])
