#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edgetunnel VPS 独立节点管理与订阅服务

提供:
  GET  /               -> 302 重定向到 /admin
  GET  /login          -> 渲染登录页
  POST /login          -> 验证管理员密码并写入 Session Cookie
  GET  /logout         -> 清理 Cookie
  GET  /admin          -> 渲染管理面板 (查看核心参数/编辑 ADD.txt 与 ADDAPI)
  POST /admin          -> 保存更新 ADD.txt 与配置
  GET  /sub            -> Clash / Mihomo 标准订阅生成器 (?token=...)
  GET  /static/<path>  -> 静态资源响应
"""

import hmac
import hashlib
import json
import mimetypes
import os
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

# 初始化签名密钥
if not os.path.exists(SECRET_KEY_PATH):
    with open(SECRET_KEY_PATH, "w", encoding="utf-8") as f:
        f.write(secrets.token_hex(32))
with open(SECRET_KEY_PATH, "r", encoding="utf-8") as f:
    SECRET_KEY = f.read().strip().encode("utf-8")

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
        except Exception as e:
            # 本地无 xray 命令时的安全 fallback
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


# 初始化默认优选 IP 列表
if not os.path.exists(ADD_PATH):
    init_nodes = (
        "104.16.1.1:443#移动优选01\n"
        "188.114.96.2:443#电信优选02\n"
        "www.spacex.com:443#SpaceX优选\n"
        "8.39.214.68:443#CF优选04\n"
        "185.146.173.66:443#CF优选05\n"
    )
    # 如果根目录存在 nodes.txt 则优先拷贝
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
        # 校验时效（7 天）
        try:
            ts = int(val.split(":", 1)[1])
            if time.time() - ts < 86400 * 7:
                return True
        except Exception:
            pass
    return False


def render_template(template_name, context=None):
    if context is None:
        context = {}
    path = os.path.join(BASE_DIR, "templates", template_name)
    if not os.path.exists(path):
        return f"<h1>Template {template_name} Not Found</h1>".encode("utf-8")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    # 处理简单的条件渲染 {% if var %}...{% endif %}
    for var, val in context.items():
        if val:
            html = re.sub(rf"\{{%\s*if\s+{var}\s*%\}}(.*?)\{{%\s*endif\s*%\}}", r"\1", html, flags=re.DOTALL)
        else:
            html = re.sub(rf"\{{%\s*if\s+{var}\s*%\}}(.*?)\{{%\s*endif\s*%\}}", "", html, flags=re.DOTALL)
    # 清理其余未匹配的 if
    html = re.sub(r"\{%\s*if\s+\w+\s*%\}.*?\{%\s*endif\s*%\}", "", html, flags=re.DOTALL)

    # 处理变量插值 {{ var }}
    for k, v in context.items():
        html = html.replace(f"{{{{ {k} }}}}", str(v))
        html = html.replace(f"{{{{{k}}}}}", str(v))
    return html.encode("utf-8")


def parse_node_line(line, default_port=443):
    """解析 IP:端口#备注 或 域名:端口#备注"""
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
    """生成标准 Clash / Mihomo YAML 订阅"""
    raw_lines = add_txt.splitlines()

    # 如果有远程 API，拉取合并
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

    # 1. 注入 🚀 VPS-Reality 极速直连节点
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

    # 2. 注入 ☁️ CF Anycast 优选节点池
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

    # 3. 构造策略组
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

    # 4. 构造分流规则
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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. 静态资源路由
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

        # 2. 根路径重定向
        if path == "/":
            self.redirect("/admin")
            return

        # 3. 登录页面
        if path == "/login":
            if self.is_authenticated():
                self.redirect("/admin")
                return
            body = render_template("login.html")
            self.send_resp(200, "text/html; charset=utf-8", body)
            return

        # 4. 登出
        if path == "/logout":
            self.redirect("/login", set_cookie="auth=; Path=/; Max-Age=0; HttpOnly")
            return

        # 5. 管理页面
        if path == "/admin":
            if not self.is_authenticated():
                self.redirect("/login")
                return
            cfg = load_config()
            add_txt = ""
            if os.path.exists(ADD_PATH):
                with open(ADD_PATH, "r", encoding="utf-8") as f:
                    add_txt = f.read()
            addapi_txt = ""
            if os.path.exists(ADDAPI_PATH):
                with open(ADDAPI_PATH, "r", encoding="utf-8") as f:
                    addapi_txt = f.read()

            host_header = self.headers.get("Host", cfg.get("domain", "node.ffly.ccwu.cc"))
            proto = "https" if "https" in self.headers.get("X-Forwarded-Proto", "http") or ":443" in host_header else "http"
            sub_url = f"{proto}://{host_header}/sub?token={cfg['sub_token']}"

            context = {
                "uuid": cfg["uuid"],
                "token": cfg["sub_token"],
                "domain": cfg["domain"],
                "reality_port": cfg["reality_port"],
                "reality_public_key": cfg["reality_public_key"],
                "ws_path": cfg["ws_path"],
                "sub_url": sub_url,
                "add_txt": add_txt,
                "addapi_txt": addapi_txt,
                "msg": query.get("msg", [""])[0],
            }
            body = render_template("admin.html", context)
            self.send_resp(200, "text/html; charset=utf-8", body)
            return

        # 6. 订阅生成接口
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

        # 读取表单数据
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="ignore")
        form_data = urllib.parse.parse_qs(raw_body)

        # 1. 登录处理
        if path == "/login":
            pwd = form_data.get("password", [""])[0]
            cfg = load_config()
            if pwd == cfg.get("admin_password", "admin123"):
                session_val = f"admin:{int(time.time())}"
                signed_cookie = sign_token(session_val)
                cookie_str = f"auth={signed_cookie}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"
                self.redirect("/admin", set_cookie=cookie_str)
                return
            # 密码错误
            body = render_template("login.html", {"error": "密码错误，请重新输入"})
            self.send_resp(401, "text/html; charset=utf-8", body)
            return

        # 2. 管理配置保存
        if path == "/admin":
            if not self.is_authenticated():
                self.redirect("/login")
                return
            add_txt = form_data.get("add_txt", [""])[0]
            addapi_txt = form_data.get("addapi_txt", [""])[0]

            with open(ADD_PATH, "w", encoding="utf-8") as f:
                f.write(add_txt.replace("\r\n", "\n"))
            with open(ADDAPI_PATH, "w", encoding="utf-8") as f:
                f.write(addapi_txt.replace("\r\n", "\n"))

            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ADD.txt & ADDAPI updated successfully")
            self.redirect("/admin?msg=" + urllib.parse.quote("配置与优选节点已保存成功！"))
            return

        self.send_resp(404, "text/plain", b"Not Found")


def run(host="0.0.0.0", port=8787):
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"[*] edgetunnel VPS Sub Service listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down server")
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8787))
    run("0.0.0.0", port)
