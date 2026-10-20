"""端到端自检：连上 NapCat 的 OneBot WS，调 API 验证登录状态"""
import asyncio
import json
import sys

import websockets


async def main():
    try:
        async with websockets.connect("ws://napcat:3001", open_timeout=8) as ws:
            print("WS_OK")

            async def call(action, params=None, timeout=8):
                await ws.send(json.dumps({"action": action, "params": params or {}, "echo": action}))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    data = json.loads(raw)
                    if data.get("echo") == action:
                        return data

            info = await call("get_login_info")
            d = info.get("data") or {}
            print("login:", info.get("status"), "qq=", d.get("user_id"), "nick=", d.get("nickname"))

            st = await call("get_status")
            print("status:", st.get("status"), json.dumps(st.get("data"), ensure_ascii=False)[:120])

            friends = await call("get_friend_list")
            fl = friends.get("data") or []
            print("friends:", len(fl))

            ver = await call("get_version_info")
            print("version:", json.dumps(ver.get("data"), ensure_ascii=False)[:150])
    except Exception as e:
        print("FAIL:", type(e).__name__, str(e)[:200])
        sys.exit(1)


asyncio.run(main())
