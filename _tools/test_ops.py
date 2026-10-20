"""测试服务器操作会话模式（不依赖 QQ，直接驱动 OpsSessionManager）

用法：
    ADMIN_QQ=你的QQ号 AGENT_URL=http://... AGENT_TOKEN=xxx python test_ops.py
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from app.core.serverops import ServerOps
from app.core.opssession import OpsSessionManager

ADMIN = os.getenv("ADMIN_QQ", "10000")
ENTRY = os.getenv("OPS_ENTRY", "/help me for linux by ra 666")
EXIT = os.getenv("OPS_EXIT", "/exit help")


async def main():
    sent = []

    async def fake_send(uid, msg):
        sent.append(msg)

    ops = ServerOps(os.getenv("AGENT_URL", "http://127.0.0.1:7788"),
                    os.getenv("AGENT_TOKEN", ""), 30)
    ok = await ops.health()
    print("agent health:", ok)

    mgr = OpsSessionManager(ops, fake_send, [ADMIN], ENTRY, EXIT, idle_seconds=3)

    print("\n1) 非管理员发口令:", await mgr.handle(ENTRY, "999", True))
    print("2) 管理员群聊发口令:", await mgr.handle(ENTRY, ADMIN, False))

    r = await mgr.handle(ENTRY, ADMIN, True)
    print("3) 管理员私聊进入:", (r or "")[:60].replace("\n", " / "))

    r = await mgr.handle("hostname && uptime && whoami", ADMIN, True)
    print("4) 模式内执行命令:")
    print(r)

    r = await mgr.handle(EXIT, ADMIN, True)
    print("5) 手动退出:", r)

    r = await mgr.handle("hostname", ADMIN, True)
    print("6) 退出后再发命令（应为 None）:", r)

    # 超时退出测试
    await mgr.handle(ENTRY, ADMIN, True)
    r = await mgr.handle("echo timeout-test", ADMIN, True)
    print("7) 重新进入并执行:", (r or "").strip().split("\n")[-1])
    print("   等待空闲超时...")
    await asyncio.sleep(5)
    print("   超时通知已发送:", sent[-1][:40].replace("\n", " ") if sent else "无")
    r = await mgr.handle("echo after-timeout", ADMIN, True)
    print("8) 超时后发命令（应为 None）:", r)


asyncio.run(main())
