# -*- coding: utf-8 -*-
"""
Google Antigravity Token 消耗全景仪表盘 - 本地 HTTP 服务端
支持本地端口访问 (默认 8765 端口)、防强缓存实时渲染、动态扫描刷新与系统物理级文件管理器直通穿透
"""

import os
import sys
import json
import shutil
import subprocess
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 引入同目录下的生成器模块
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
import generator

DEFAULT_PORT = 8765

class AntigravityDashboardHandler(BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        # 简化控制台日志输出
        sys.stdout.write(f"[{self.log_date_time_string()}] {args[0]} - {args[1]}\n")
        sys.stdout.flush()

    def send_cors_and_nocache_headers(self, content_type="text/html; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # 强制彻底禁用一切浏览器缓存，杜绝“改完没反应”
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 根路径：返回最新的 index.html
        if path in ("/", "/index.html"):
            html_path = os.path.join(CURRENT_DIR, "index.html")
            if not os.path.exists(html_path):
                # 若尚未生成，立即执行一次完整构建
                antigravity_dir = generator.detect_antigravity_dir(None)
                summary, conversations = generator.load_data(antigravity_dir)
                html_content = generator.generate_html(summary, conversations)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
            
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.send_cors_and_nocache_headers("text/html; charset=utf-8")
            self.wfile.write(content.encode("utf-8"))
            return

        # 获取数据 JSON
        elif path == "/api/data":
            json_path = os.path.join(CURRENT_DIR, "data.json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = f.read()
            else:
                antigravity_dir = generator.detect_antigravity_dir(None)
                summary, conversations = generator.load_data(antigravity_dir)
                data = json.dumps({"summary": summary, "conversations": conversations}, ensure_ascii=False)
            
            self.send_cors_and_nocache_headers("application/json; charset=utf-8")
            self.wfile.write(data.encode("utf-8"))
            return

        # 状态探测
        elif path == "/api/status":
            res = {"status": "ok", "service": "antigravity-dashboard", "port": self.server.server_port}
            self.send_cors_and_nocache_headers("application/json; charset=utf-8")
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. 动态重新扫描并刷新全量看板
        if path == "/api/refresh":
            try:
                antigravity_dir = generator.detect_antigravity_dir(None)
                summary, conversations = generator.load_data(antigravity_dir)
                
                # 重新写入 data.json 与 index.html
                json_path = os.path.join(CURRENT_DIR, "data.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump({"summary": summary, "conversations": conversations}, f, ensure_ascii=False, indent=2)

                html_content = generator.generate_html(summary, conversations)
                html_path = os.path.join(CURRENT_DIR, "index.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)

                res = {
                    "success": True,
                    "message": "数据已重新扫描并更新完成",
                    "summary": summary,
                    "conversations_count": len(conversations)
                }
                self.send_cors_and_nocache_headers("application/json; charset=utf-8")
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, f"Refresh error: {str(e)}")
            return

        # 2. 物理桌面文件管理器穿透呼起 (支持 Directory Opus 与 Explorer)
        elif path == "/api/open-folder":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                payload = json.loads(body) if body else {}
                target_path = payload.get("path", "")
                mode = payload.get("mode", "dopus") # 'dopus' or 'explorer'

                clean_path = target_path.replace("/.system_generated/logs/transcript.jsonl", "")
                clean_path = clean_path.replace("/", "\\")

                success = False
                msg = ""

                # 优先尝试使用 Directory Opus
                if mode == "dopus":
                    dopus_rt = shutil.which("dopusrt.exe") or r"C:\Program Files\GPSoftware\Directory Opus\dopusrt.exe"
                    if os.path.exists(dopus_rt) or shutil.which("dopusrt.exe"):
                        cmd = f'"{dopus_rt}" /cmd Go OPENCONTAINER PATH "{clean_path}" NEWTAB=deflister,findexisting TOFRONT'
                        subprocess.Popen(cmd, shell=True)
                        success = True
                        msg = "已通过 Directory Opus 唤醒并高亮选中目标目录"
                    else:
                        # 降级使用 Windows Explorer
                        cmd = f'explorer.exe /select,"{clean_path}"'
                        subprocess.Popen(cmd, shell=True)
                        success = True
                        msg = "未检测到 DOpus，已优雅降级唤醒 Windows 资源管理器"
                else:
                    cmd = f'explorer.exe /select,"{clean_path}"'
                    subprocess.Popen(cmd, shell=True)
                    success = True
                    msg = "已唤醒 Windows 资源管理器并高亮选中目标目录"

                res = {"success": success, "message": msg, "path": clean_path}
                self.send_cors_and_nocache_headers("application/json; charset=utf-8")
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self.send_error(500, f"Open folder error: {str(e)}")
            return

        else:
            self.send_error(404, "Not Found")

def start_server(port=DEFAULT_PORT):
    # 如果指定端口已被占用，自动递增探测
    server = None
    for p in range(port, port + 20):
        try:
            server = HTTPServer(("127.0.0.1", p), AntigravityDashboardHandler)
            port = p
            break
        except OSError:
            continue

    if not server:
        print(f"❌ 无法绑定端口 {port} 至 {port+20}，请检查端口占用。")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print("=" * 68)
    print("🚀 Google Antigravity Token 看板本地 Web 服务已就绪！")
    print(f"🌐 本地访问地址: {url}")
    print("⚡ 特性: 彻底根除浏览器强缓存 · 支持无刷新重新扫描 · 桌面 DOpus 穿透")
    print("=" * 68)
    print("按 Ctrl + C 可终止本地服务。")

    # 自动唤起物理桌面默认浏览器打开该端口页面
    def open_browser():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    import threading
    threading.Timer(0.8, open_browser).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 服务已安全停止。")
        server.server_close()

if __name__ == "__main__":
    port_arg = DEFAULT_PORT
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port_arg = int(sys.argv[1])
    start_server(port_arg)
