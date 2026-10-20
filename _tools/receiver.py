#!/usr/bin/env python3
"""服务器端文件接收器 —— 监听本地端口，接收 base64 分块并写入文件

配合本地 pushfiles.ps1 使用：本地一次 POST 整个文件，避免控制台逐字符传输。
只绑 127.0.0.1，通过 SSH 隧道或 docker 网关访问。
"""

import base64
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND = ("127.0.0.1", 7799)
ROOT = os.environ.get("RECV_ROOT", "/root/qqchat")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _j(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.path != "/put":
            return self._j(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n))
        except Exception as e:
            return self._j(400, {"error": f"bad request: {e}"})

        path = (payload.get("path") or "").lstrip("/")
        data_b64 = payload.get("data") or ""
        expect_sha = payload.get("sha256") or ""
        if not path:
            return self._j(400, {"error": "missing path"})

        # 防目录穿越
        target = os.path.normpath(os.path.join(ROOT, path))
        if not target.startswith(os.path.normpath(ROOT)):
            return self._j(403, {"error": "path escape denied"})

        try:
            raw = base64.b64decode(data_b64)
        except Exception as e:
            return self._j(400, {"error": f"bad base64: {e}"})

        if expect_sha:
            got = hashlib.sha256(raw).hexdigest()
            if got != expect_sha:
                return self._j(409, {"error": "sha mismatch", "expect": expect_sha, "got": got})

        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(raw)
        print(f"WROTE {target} {len(raw)}", flush=True)
        return self._j(200, {"ok": True, "path": target, "size": len(raw)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"receiver on {BIND[0]}:{BIND[1]} root={ROOT}", flush=True)
    ThreadingHTTPServer(BIND, H).serve_forever()
