"""探测 NapCat 支持的所有 OneBot API，并测试关键能力的实际返回值"""
import asyncio
import json

import websockets

PROBES = [
    # 群相关
    ("get_group_info", {}),
    ("get_group_list", {}),
    ("get_group_member_info", {}),
    ("get_group_member_list", {}),
    ("get_group_honor_info", {}),
    ("get_group_system_msg", {}),
    ("get_group_at_all_remain", {}),
    # 消息
    ("get_msg", {}),
    ("get_forward_msg", {}),
    ("get_group_msg_history", {}),
    ("get_friend_msg_history", {}),
    ("get_recent_contact", {}),
    # 用户
    ("get_login_info", {}),
    ("get_friend_list", {}),
    ("get_stranger_info", {}),
    ("get_status", {}),
    ("get_version_info", {}),
    ("get_cookies", {}),
    ("get_csrf_token", {}),
    ("get_credentials", {}),
    # 群操作
    ("get_essence_msg_list", {}),
    ("get_group_notice", {}),
    # 其他
    ("can_send_image", {}),
    ("can_send_record", {}),
    ("get_online_clients", {}),
    ("get_robot_uin_range", {}),
]


async def main():
    async with websockets.connect("ws://napcat:3001", open_timeout=10) as ws:
        async def call(action, params=None, timeout=6):
            echo = f"e_{action}"
            await ws.send(json.dumps({"action": action, "params": params or {}, "echo": echo}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                d = json.loads(raw)
                if d.get("echo") == echo:
                    return d

        print(f"{'API':32} {'status':10} data_preview")
        print("-" * 90)
        for action, params in PROBES:
            try:
                r = await call(action, params)
                st = r.get("status", "?")
                data = r.get("data")
                if isinstance(data, (dict, list)):
                    prev = json.dumps(data, ensure_ascii=False)[:80]
                else:
                    prev = str(data)[:80]
                print(f"{action:32} {st:10} {prev}")
            except asyncio.TimeoutError:
                print(f"{action:32} {'TIMEOUT':10}")
            except Exception as e:
                print(f"{action:32} {'ERR':10} {str(e)[:50]}")


asyncio.run(main())
