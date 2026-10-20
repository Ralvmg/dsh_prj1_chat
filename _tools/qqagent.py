#!/usr/bin/env python3
"""QQChat 宿主机操作代理

只监听内网地址（docker 网关），带令牌校验，供 app 容器调用执行服务器命令。
所有命令记录到 /var/log/qqagent.log。

接口：
  GET  /health          → {"ok": true}
  POST /exec            → {"cmd": "...", "timeout": 60} → {"code":0,"out":"..."}
"""

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_HOST = os.environ.get("QQAGENT_HOST", "172.18.0.1")
BIND_PORT = int(os.environ.get("QQAGENT_PORT", "7788"))
TOKEN = os.environ.get("QQAGENT_TOKEN", "")
LOG_PATH = os.environ.get("QQAGENT_LOG", "/var/log/qqagent.log")
MAX_OUT = 8000
MAX_TIMEOUT = 120

# 灾难性命令直接拒绝（需要 !!! 前缀强行放行）
BLOCK_PATTERNS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=/dev/zero of=/dev/",
    ":(){", "> /dev/sda", "chmod -R 777 /",
]


def log(msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("X-Token", "") == TOKEN

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/exec":
            return self._json(404, {"error": "not found"})
        if not self._auth():
            log("DENY bad token")
            return self._json(403, {"error": "bad token"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            return self._json(400, {"error": f"bad json: {e}"})

        cmd = (payload.get("cmd") or "").strip()
        timeout = min(int(payload.get("timeout") or 60), MAX_TIMEOUT)
        if not cmd:
            return self._json(400, {"error": "empty cmd"})

        # 灾难性命令保护
        low = cmd.lower()
        force = cmd.startswith("!!!")
        real_cmd = cmd[3:].strip() if force else cmd
        if not force:
            for pat in BLOCK_PATTERNS:
                if pat in low:
                    log(f"BLOCK {cmd[:120]}")
                    return self._json(200, {
                        "code": 126,
                        "out": f"已拦截危险命令（{pat}）。确需执行请用 !!! 前缀。",
                    })

        log(f"EXEC {real_cmd[:300]}")
        t0 = time.time()
        try:
            p = subprocess.run(
                ["/bin/bash", "-lc", real_cmd],
                capture_output=True, text=True, timeout=timeout,
                cwd=os.environ.get("QQAGENT_CWD", "/root"),
            )
            out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
            code = p.returncode
        except subprocess.TimeoutExpired:
            out, code = f"命令超时（>{timeout}s）", 124
        except Exception as e:
            out, code = f"执行异常: {e}", 1

        dt = time.time() - t0
        log(f"DONE code={code} {dt:.1f}s")
        if len(out) > MAX_OUT:
            out = out[:MAX_OUT] + f"\n…（输出截断，共 {len(out)} 字符）"
        return self._json(200, {"code": code, "out": out, "elapsed": round(dt, 2)})

    def log_message(self, fmt, *args):
        pass  # 不刷默认访问日志


if __name__ == "__main__":
    log(f"qqagent starting on {BIND_HOST}:{BIND_PORT} token={'set' if TOKEN else 'none'}")
    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    srv.serve_forever()
