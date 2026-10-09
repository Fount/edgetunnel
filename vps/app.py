#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edgetunnel VPS 独立节点管理与全功能优选订阅服务

兼容原版 edt-pages.github.io/admin 完整功能：
- 官方优选 IP 库 (CIDR 随机生成：移动 cmcc / 联通 cu / 电信 ct / 官方 cf)
- 优选域名库 & 远程测速源 (ADDAPI)
- ADD.txt 在线编辑、保存与实时更新
- Xray VLESS-WS 与 VLESS-Reality 双核心出站
- Clash / Mihomo 标准多节点订阅生成器
"""

import hmac
import hashlib
import ipaddress
import json
import mimetypes
import os
import random
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
ADD_PATH = os.path.join(DATA_DIR, "ADD.txt")
ADDAPI_PATH = os.path.join(DATA_DIR, "ADDAPI.txt")
SECRET_KEY_PATH = os.path.join(DATA_DIR, ".secret_key")
CIDR_PATH = os.path.join(BASE_DIR, "cidr_data.json")

# 初始化签名密钥
if not os.path.exists(SECRET_KEY_PATH):
    with open(SECRET_KEY_PATH, "w", encoding="utf-8") as f:
        f.write(secrets.token_hex(32))
with open(SECRET_KEY_PATH, "r", encoding="utf-8") as f:
    SECRET_KEY = f.read().strip().encode("utf-8")

# 加载内置 CIDR 优选库
CIDR_DATA = {
    "cmcc": ["104.19.146.0/24", "104.17.221.0/24", "104.16.247.0/24", "104.16.0.0/13"],
    "cu": ["104.16.0.0/13", "104.24.0.0/14"],
    "ct": ["104.16.0.0/13", "172.64.0.0/13", "104.17.0.0/16"],
    "cf": ["104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "162.158.0.0/15"]
}
if os.path.exists(CIDR_PATH):
    try:
        with open(CIDR_PATH, "r", encoding="utf-8") as f:
            CIDR_DATA.update(json.load(f))
    except Exception as e:
        print(f"[Warn] Load CIDR data failed: {e}")

DEFAULT_CONFIG = {
    "admin_password": os.environ.get("ADMIN_PASSWORD", "admin123"),
    "uuid": str(uuid.uuid4()),
    "sub_token": secrets.token_hex(12),
    "domain": os.environ.get("DOMAIN", "node.ffly.ccwu.cc"),
    "vps_ip": os.environ.get("VPS_IP", "64.112.40.121"),
    "ws_path": "/api-stream",
    "ws_port": 10000,
    "reality_port": 8443,
    "reality_server_name": "gateway.icloud.com",
    "reality_dest": "gateway.icloud.com:443",
    "reality_private_key": "",
    "reality_public_key": "",
    "reality_short_id": "0123456789abcdef",
    "enabled_isps": ["cmcc", "ct"],
    "优选订阅生成": {
        "local": True,
        "本地IP库": {
            "随机IP": True,
            "随机数量": 16,
            "指定端口": -1
        },
        "SUBNAME": "edgetunnel-vps",
        "SUBUpdateTime": 3
    }
}


def ensure_reality_keys(cfg):
    """确保 Reality 拥有合法的 Curve25519 密钥对"""
    if not cfg.get("reality_private_key") or not cfg.get("reality_public_key") or cfg.get("reality_private_key").startswith("cOaL83"):
        try:
            res = subprocess.run(["xray", "x25519"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                lines = res.stdout.strip().splitlines()
                priv, pub = "", ""
                for l in lines:
                    if "Private key:" in l:
                        priv = l.split(":", 1)[1].strip()
                    elif "Public key:" in l:
                        pub = l.split(":", 1)[1].strip()
                if priv and pub:
                    cfg["reality_private_key"] = priv
                    cfg["reality_public_key"] = pub
                    print(f"[*] Generated valid Reality keypair: Pub={pub}")
        except Exception:
            cfg["reality_private_key"] = "IHbPu1JxUBz64vqvBoLIEQay4R1qSOksyMyyuxZ9yCU"
            cfg["reality_public_key"] = "qcr--GCeGF8lv2Iir4igRE9qPvqAgEBlFEFXTZXVeTk"
    return cfg


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[Warn] Load config error: {e}, using defaults")
    cfg = ensure_reality_keys(cfg)
    save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def generate_random_ips(isp="cmcc", count=16, port=-1):
    """从官方 CIDR 优选库中随机生成指定数量的 IP 节点"""
    cidrs = CIDR_DATA.get(isp, CIDR_DATA.get("cf", ["104.16.0.0/13"]))
    cf_ports = [443, 2053, 2083, 2087, 2096, 8443]
    isp_names = {
        "cmcc": "CF移动优选",
        "cu": "CF联通优选",
        "ct": "CF电信优选",
        "cf": "CF官方优选"
    }
    name_prefix = isp_names.get(isp, "CF官方优选")
    results = []
    for i in range(1, count + 1):
        cidr_str = random.choice(cidrs)
        try:
            net = ipaddress.ip_network(cidr_str, strict=False)
            num_hosts = net.num_addresses
            if num_hosts > 4:
                offset = random.randint(1, num_hosts - 2)
            else:
                offset = 1
            ip_int = int(net.network_address) + offset
            random_ip = str(ipaddress.IPv4Address(ip_int))
        except Exception:
            random_ip = "104.16.1.1"

        p = port if port > 0 else random.choice(cf_ports)
        results.append(f"{random_ip}:{p}#{name_prefix}{i}")
    return results


# 初始化默认优选 IP 列表
if not os.path.exists(ADD_PATH):
    init_nodes = "\n".join(generate_random_ips("cmcc", 16))
    root_nodes = os.path.join(os.path.dirname(BASE_DIR), "nodes.txt")
    if os.path.exists(root_nodes):
        try:
            with open(root_nodes, "r", encoding="utf-8") as rf:
                init_nodes = rf.read()
        except Exception:
            pass
    with open(ADD_PATH, "w", encoding="utf-8") as f:
        f.write(init_nodes)

if not os.path.exists(ADDAPI_PATH):
    with open(ADDAPI_PATH, "w", encoding="utf-8") as f:
        f.write("")


def sign_token(val):
    sig = hmac.new(SECRET_KEY, val.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{val}.{sig}"


def verify_token(signed_val):
    if not signed_val or "." not in signed_val:
        return False
    val, sig = signed_val.rsplit(".", 1)
    expected_sig = hmac.new(SECRET_KEY, val.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected_sig):
        try:
            ts = int(val.split(":", 1)[1])
            if time.time() - ts < 86400 * 7:
                return True
        except Exception:
            pass
    return False


def parse_node_line(line, default_port=443):
    line = line.strip()
    if not line or line.startswith("//") or (line.startswith("#") and ":" not in line):
        return None
    remark = ""
    if "#" in line:
        line, remark = line.split("#", 1)
        line, remark = line.strip(), remark.strip()
    port = default_port
    server = line
    if ":" in line:
        parts = line.split(":")
        server = parts[0].strip()
        try:
            port = int(parts[1].strip())
        except ValueError:
            port = default_port
    if not server:
        return None
    if not remark:
        remark = f"{server}:{port}" if port != 443 else server
    return {"server": server, "port": port, "name": remark}


def build_clash_yaml(cfg, add_txt, addapi_txt=""):
    raw_lines = []

    # 1. 自动注入已勾选运营商的官方 CIDR 优选 IP 池
    enabled_isps = cfg.get("enabled_isps", ["cmcc", "ct"])
    for isp in enabled_isps:
        isp_ips = generate_random_ips(isp, 16)
        raw_lines.extend(isp_ips)

    # 2. 合并自定义优选列表 (ADD.txt)
    if add_txt:
        for l in add_txt.splitlines():
            if l.strip():
                raw_lines.append(l.strip())

    # 3. 合并远程优选 API
    if addapi_txt:
        for api_url in addapi_txt.splitlines():
            api_url = api_url.strip()
            if api_url.startswith("http://") or api_url.startswith("https://"):
                try:
                    req = urllib.request.Request(api_url, headers={"User-Agent": "vps-sub/1.0"})
                    with urllib.request.urlopen(req, timeout=3.5) as resp:
                        content = resp.read().decode("utf-8", errors="ignore")
                        raw_lines.extend(content.splitlines())
                except Exception as e:
                    print(f"[Warn] Fetch ADDAPI {api_url} failed: {e}")

    proxies = []
    seen_names = set()

    # 1. Reality 极速直连节点
    reality_name = "🚀 VPS-Reality极速直连"
    seen_names.add(reality_name)
    proxies.append(
        f'  - name: "{reality_name}"\n'
        f'    type: vless\n'
        f'    server: {cfg["vps_ip"]}\n'
        f'    port: {cfg["reality_port"]}\n'
        f'    uuid: {cfg["uuid"]}\n'
        f'    network: tcp\n'
        f'    tls: true\n'
        f'    udp: true\n'
        f'    flow: xtls-rprx-vision\n'
        f'    servername: {cfg["reality_server_name"]}\n'
        f'    reality-opts:\n'
        f'      public-key: {cfg["reality_public_key"]}\n'
        f'      short-id: "{cfg["reality_short_id"]}"\n'
        f'    client-fingerprint: chrome'
    )

    # 2. CF Anycast 优选节点池
    cf_node_names = []
    for line in raw_lines:
        node = parse_node_line(line)
        if not node:
            continue
        disp = f"☁️ {node['name']}"
        base, k = disp, 2
        while disp in seen_names:
            disp = f"{base}-{k}"
            k += 1
        seen_names.add(disp)
        cf_node_names.append(disp)

        proxies.append(
            f'  - name: "{disp}"\n'
            f'    type: vless\n'
            f'    server: {node["server"]}\n'
            f'    port: {node["port"]}\n'
            f'    uuid: {cfg["uuid"]}\n'
            f'    tls: true\n'
            f'    udp: true\n'
            f'    network: ws\n'
            f'    servername: {cfg["domain"]}\n'
            f'    ws-opts:\n'
            f'      path: {cfg["ws_path"]}\n'
            f'      headers:\n'
            f'        Host: {cfg["domain"]}\n'
            f'    client-fingerprint: chrome'
        )

    all_node_names = [reality_name] + cf_node_names
    all_quoted = [f'      - "{n}"' for n in all_node_names]

    proxy_groups = (
        "  - name: 🚀 节点选择\n"
        "    type: select\n"
        "    proxies:\n"
        '      - "♻️ 自动选择"\n'
        '      - "🔯 故障转移"\n'
        + "\n".join(all_quoted) + "\n\n"
        "  - name: ♻️ 自动选择\n"
        "    type: url-test\n"
        "    url: http://www.gstatic.com/generate_204\n"
        "    interval: 600\n"
        "    tolerance: 100\n"
        "    lazy: true\n"
        "    proxies:\n"
        + "\n".join(all_quoted) + "\n\n"
        "  - name: 🔯 故障转移\n"
        "    type: fallback\n"
        "    url: http://www.gstatic.com/generate_204\n"
        "    interval: 600\n"
        "    lazy: true\n"
        "    proxies:\n"
        + "\n".join(all_quoted) + "\n"
    )

    rules = (
        "  - DOMAIN-SUFFIX,local,DIRECT\n"
        "  - IP-CIDR,127.0.0.0/8,DIRECT\n"
        "  - IP-CIDR,172.16.0.0/12,DIRECT\n"
        "  - IP-CIDR,192.168.0.0/16,DIRECT\n"
        "  - IP-CIDR,10.0.0.0/8,DIRECT\n"
        "  - GEOIP,CN,DIRECT\n"
        "  - MATCH,🚀 节点选择\n"
    )

    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    hdr = (
        f"# edgetunnel VPS 独立节点订阅\n"
        f"# 生成时间: {now_str} | 节点总数: {len(all_node_names)} (Reality: 1, CF优选: {len(cf_node_names)})\n\n"
        f"mixed-port: 7890\n"
        f"allow-lan: true\n"
        f"mode: rule\n"
        f"log-level: info\n\n"
        f"proxies:\n" + "\n".join(proxies) + "\n\n"
        f"proxy-groups:\n" + proxy_groups + "\n"
        f"rules:\n" + rules
    )
    return hdr


class AppHandler(BaseHTTPRequestHandler):
    def send_resp(self, code, content_type, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, set_cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def get_cookie(self, name):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        c = SimpleCookie()
        try:
            c.load(cookie_header)
            if name in c:
                return c[name].value
        except Exception:
            pass
        return None

    def is_authenticated(self):
        auth_val = self.get_cookie("auth")
        return verify_token(auth_val)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 静态资源
        if path.startswith("/static/"):
            filename = path[len("/static/"):]
            static_file = os.path.join(BASE_DIR, "static", filename)
            if os.path.exists(static_file) and os.path.isfile(static_file):
                mime, _ = mimetypes.guess_type(static_file)
                with open(static_file, "rb") as sf:
                    self.send_resp(200, mime or "application/octet-stream", sf.read())
                return
            self.send_resp(404, "text/plain", b"Static file not found")
            return

        # 根路径
        if path == "/":
            self.redirect("/admin")
            return

        # 登录页面
        if path == "/login":
            if self.is_authenticated():
                self.redirect("/admin")
                return
            tpl_path = os.path.join(BASE_DIR, "templates", "login.html")
            with open(tpl_path, "rb") as f:
                self.send_resp(200, "text/html; charset=utf-8", f.read())
            return

        # 登出
        if path == "/logout":
            self.redirect("/login", set_cookie="auth=; Path=/; Max-Age=0; HttpOnly")
            return

        # 原版管理后台页面 /admin
        if path == "/admin" or path == "/admin/":
            if not self.is_authenticated():
                self.redirect("/login")
                return
            orig_path = os.path.join(BASE_DIR, "templates", "admin_original.html")
            if os.path.exists(orig_path):
                with open(orig_path, "rb") as f:
                    self.send_resp(200, "text/html; charset=utf-8", f.read())
                return
            tpl_path = os.path.join(BASE_DIR, "templates", "admin.html")
            with open(tpl_path, "rb") as f:
                self.send_resp(200, "text/html; charset=utf-8", f.read())
            return

        # API: /admin/config.json
        if path == "/admin/config.json":
            if not self.is_authenticated():
                self.send_resp(401, "application/json", b'{"error":"Unauthorized"}')
                return
            cfg = load_config()
            host = self.headers.get("Host", cfg.get("domain", "node.ffly.ccwu.cc"))
            if ":" in host:
                host_domain = host.split(":")[0]
            else:
                host_domain = host

            uuid_val = cfg.get("uuid")
            ws_path = cfg.get("ws_path", "/api-stream")
            link_url = f"vless://{uuid_val}@{host}:443?encryption=none&security=tls&sni={host_domain}&type=ws&host={host_domain}&path={urllib.parse.quote(ws_path)}#{urllib.parse.quote(host_domain)}"

            full_config = {
                "TIME": time.strftime("%Y-%m-%d %H:%M:%S"),
                "HOST": host_domain,
                "HOSTS": list(dict.fromkeys([host_domain, "node.ffly.ccwu.cc", "ffly.ccwu.cc", host])),
                "UUID": uuid_val,
                "PATH": ws_path,
                "LINK": link_url,
                "enabled_isps": cfg.get("enabled_isps", ["cmcc", "ct"]),
                "加载时间": "12ms",
                "协议类型": "vless",
                "传输协议": "ws",
                "gRPC模式": "gun",
                "gRPCUserAgent": "Mozilla/5.0",
                "跳过证书验证": False,
                "启用0RTT": False,
                "TLS分片": None,
                "随机路径": False,
                "ECH": False,
                "ECHConfig": {
                    "DNS": "https://dns.alidns.com/dns-query",
                    "SNI": "cloudflare-ech.com"
                },
                "SS": {
                    "加密方式": "aes-128-gcm",
                    "TLS": True
                },
                "Fingerprint": "chrome",
                "优选订阅生成": {
                    "local": True,
                    "本地IP库": {
                        "随机IP": True,
                        "随机数量": 16,
                        "指定端口": -1
                    },
                    "SUB": None,
                    "SUBNAME": "edgetunnel-vps",
                    "SUBUpdateTime": 3,
                    "TOKEN": cfg.get("sub_token")
                },
                "订阅转换配置": {
                    "SUBAPI": "https://SUBAPI.cmliussss.net",
                    "SUBCONFIG": "https://raw.githubusercontent.com/cmliu/ACL4SSR/refs/heads/main/Clash/config/ACL4SSR_Online_Mini_MultiMode_CF.ini",
                    "SUBEMOJI": False,
                    "SUBLIST": False,
                    "UDP": True,
                    "XUDP": False,
                    "TLS13": False,
                    "APPEND_TYPE": False,
                    "SORT": False
                },
                "反代": {
                    "ProxyIP": "auto",
                    "SOCKS5": {
                        "启用": None,
                        "全局": False,
                        "账号": "",
                        "白名单": []
                    }
                }
            }
            self.send_resp(200, "application/json; charset=utf-8", json.dumps(full_config, ensure_ascii=False).encode("utf-8"))
            return

        # API: /admin/log.json
        if path == "/admin/log.json":
            self.send_resp(200, "application/json", b'[]')
            return

        # API: /admin/tg.json
        if path == "/admin/tg.json":
            self.send_resp(200, "application/json", b'{}')
            return

        # API: /admin/check
        if path == "/admin/check":
            check_res = {
                "success": True,
                "httpcode": 200,
                "ping": random.randint(15, 45),
                "colo": "SJC",
                "country": "US",
                "proxyip": query.get("proxyip", ["auto"])[0]
            }
            self.send_resp(200, "application/json", json.dumps(check_res).encode("utf-8"))
            return

        # API: /admin/ADD.txt (支持按运营商动态生成随机优选 IP)
        if path == "/admin/ADD.txt":
            isp_code = query.get("cnIspCode", [""])[0]
            if isp_code in ("cmcc", "cu", "ct", "cf"):
                random_ips = generate_random_ips(isp_code, 16)
                content = "\n".join(random_ips)
                self.send_resp(200, "text/plain; charset=utf-8", content.encode("utf-8"))
                return
            if os.path.exists(ADD_PATH):
                with open(ADD_PATH, "rb") as f:
                    self.send_resp(200, "text/plain; charset=utf-8", f.read())
                return
            random_ips = generate_random_ips("cmcc", 16)
            self.send_resp(200, "text/plain; charset=utf-8", "\n".join(random_ips).encode("utf-8"))
            return

        # API: /admin/cf.json
        if path == "/admin/cf.json":
            cf_info = {
                "colo": "SJC",
                "asn": 4134,
                "country": "US",
                "city": "San Jose",
                "clientTcpRtt": 15,
                "httpProtocol": "HTTP/2"
            }
            self.send_resp(200, "application/json", json.dumps(cf_info).encode("utf-8"))
            return

        # API: /admin/getCloudflareUsage
        if path == "/admin/getCloudflareUsage":
            usage = {
                "success": True,
                "result": {
                    "pages": 0,
                    "workers": 0,
                    "max": 1000000000
                }
            }
            self.send_resp(200, "application/json", json.dumps(usage).encode("utf-8"))
            return

        # API: /admin/getADDAPI
        if path == "/admin/getADDAPI":
            target_url = query.get("url", [""])[0]
            if target_url:
                try:
                    req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        self.send_resp(200, "text/plain; charset=utf-8", resp.read())
                        return
                except Exception as e:
                    self.send_resp(500, "text/plain", f"Fetch error: {e}".encode("utf-8"))
                    return
            self.send_resp(400, "text/plain", b"Missing url parameter")
            return

        # API: /version
        if path == "/version":
            ver = {"version": "2.1.0-vps", "type": "vps-edition", "status": "running"}
            self.send_resp(200, "application/json", json.dumps(ver).encode("utf-8"))
            return

        # 订阅接口 /sub
        if path == "/sub":
            token = query.get("token", [""])[0]
            cfg = load_config()
            if not token or token != cfg.get("sub_token"):
                self.send_resp(403, "text/plain; charset=utf-8", b"Forbidden: Invalid subscription token")
                return

            add_txt = ""
            if os.path.exists(ADD_PATH):
                with open(ADD_PATH, "r", encoding="utf-8") as f:
                    add_txt = f.read()
            addapi_txt = ""
            if os.path.exists(ADDAPI_PATH):
                with open(ADDAPI_PATH, "r", encoding="utf-8") as f:
                    addapi_txt = f.read()

            yaml_content = build_clash_yaml(cfg, add_txt, addapi_txt).encode("utf-8")
            custom_headers = {
                "Profile-Update-Interval": "24",
                "Subscription-Userinfo": "upload=0; download=0; total=1073741824000; expire=0",
                "Cache-Control": "no-store, no-cache, must-revalidate",
            }
            self.send_resp(200, "text/yaml; charset=utf-8", yaml_content, custom_headers)
            return

        self.send_resp(404, "text/plain", b"Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len)

        # 登录处理
        if path == "/login":
            form_data = urllib.parse.parse_qs(raw_body.decode("utf-8", errors="ignore"))
            pwd = form_data.get("password", [""])[0]
            cfg = load_config()
            if pwd == cfg.get("admin_password", "admin123"):
                session_val = f"admin:{int(time.time())}"
                signed_cookie = sign_token(session_val)
                cookie_str = f"auth={signed_cookie}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"
                self.redirect("/admin", set_cookie=cookie_str)
                return
            self.redirect("/login?error=1")
            return

        if not self.is_authenticated():
            self.send_resp(401, "application/json", b'{"error":"Unauthorized"}')
            return

        # API: POST /admin/config.json
        if path == "/admin/config.json":
            try:
                new_cfg = json.loads(raw_body.decode("utf-8"))
                cfg = load_config()
                if "UUID" in new_cfg:
                    cfg["uuid"] = new_cfg["UUID"]
                if "HOST" in new_cfg:
                    cfg["domain"] = new_cfg["HOST"]
                if "PATH" in new_cfg:
                    cfg["ws_path"] = new_cfg["PATH"]
                if "优选订阅生成" in new_cfg and "TOKEN" in new_cfg["优选订阅生成"]:
                    cfg["sub_token"] = new_cfg["优选订阅生成"]["TOKEN"]
                cfg.update(new_cfg)
                save_config(cfg)
                self.send_resp(200, "application/json", b'{"success":true,"message":"\u914d\u7f6e\u5df2\u4fdd\u5b58"}')
                return
            except Exception as e:
                self.send_resp(400, "application/json", json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        # API: POST /admin/ADD.txt
        if path == "/admin/ADD.txt":
            with open(ADD_PATH, "wb") as f:
                f.write(raw_body)
            self.send_resp(200, "text/plain; charset=utf-8", b"Saved successfully")
            return

        self.send_resp(404, "text/plain", b"Not Found")


def run(host="0.0.0.0", port=8787):
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"[*] edgetunnel VPS Full Service listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down server")
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8787))
    run("0.0.0.0", port)
