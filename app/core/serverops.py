"""服务器操作客户端 —— 通过宿主机上的 qqagent 执行命令

安全设计（防误触发）：
1. 只接受"管理员QQ号"在"私聊"里发、且以严格前缀（默认 !! ）开头的消息
2. 普通聊天、群聊、其他人 → 完全不触发，LLM 也看不到这条通路
3. 宿主机代理带令牌、只绑内网地址、灾难性命令需 !!! 前缀
4. 所有命令写 /var/log/qqagent.log
"""

import json
import logging
import httpx

logger = logging.getLogger(__name__)


class ServerOps:
    def __init__(self, base_url: str, token: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = httpx.Timeout(float(timeout), connect=5.0)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self.base_url}/health",
                                headers={"X-Token": self.token})
                return r.status_code == 200 and r.json().get("ok") is True
        except Exception:
            return False

    async def exec(self, cmd: str, timeout: int = 60) -> dict:
        """执行命令，返回 {code, out, elapsed}"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    f"{self.base_url}/exec",
                    json={"cmd": cmd, "timeout": timeout},
                    headers={"X-Token": self.token},
                )
                if r.status_code != 200:
                    return {"code": -1, "out": f"代理返回 {r.status_code}: {r.text[:200]}"}
                return r.json()
        except httpx.ConnectError:
            return {"code": -1, "out": "连不上服务器代理（qqagent 可能没启动）"}
        except httpx.TimeoutException:
            return {"code": 124, "out": "命令执行超时"}
        except Exception as e:
            return {"code": -1, "out": f"执行异常: {e}"}

    async def run_and_format(self, cmd: str, timeout: int = 60) -> str:
        """执行并格式化成给 QQ 看的文本"""
        res = await self.exec(cmd, timeout=timeout)
        out = (res.get("out") or "").strip()
        code = res.get("code")
        if not out:
            out = "(无输出)"
        head = f"$ {cmd}\n"
        tail = "" if code == 0 else f"\n[退出码 {code}]"
        return head + out + tail
