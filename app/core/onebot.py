"""OneBot v11 协议客户端 —— 极简实现，只做收发消息"""

import json
import logging
import asyncio
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)


class OneBotClient:
    """OneBot v11 客户端

    NapCat 以「正向 WebSocket 服务端」模式运行，本程序作为客户端连接。
    协议文档：https://github.com/botuniverse/onebot-11
    """

    def __init__(self, ws_url: str, access_token: str = "", reconnect_delay: int = 5):
        self.ws_url = ws_url
        self.access_token = access_token
        self.reconnect_delay = reconnect_delay
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.self_id: Optional[str] = None
        self.connected: bool = False          # OneBot 是否在线（供工具判断）
        self._echo_seq = 0
        self._running = False
        self._on_message: Optional[Callable] = None
        self._pending: dict[str, asyncio.Future] = {}  # echo -> Future（API 响应等待）

    def on_message(self, callback: Callable):
        """注册消息回调（async 函数）"""
        self._on_message = callback

    async def connect(self):
        url = self.ws_url
        if self.access_token and "access_token=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}access_token={self.access_token}"

        self.ws = await websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,
        )
        # 重连后清空挂起的请求
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(None)
        self._pending.clear()
        logger.info(f"已连接 OneBot: {self.ws_url}")

    async def send_api(self, action: str, params: dict = None) -> str:
        """发送 API 请求，返回 echo（不等结果）"""
        self._echo_seq += 1
        echo = f"e{self._echo_seq}"
        if not self.ws:
            raise RuntimeError("WebSocket 未连接")
        await self.ws.send(json.dumps({
            "action": action,
            "params": params or {},
            "echo": echo,
        }))
        return echo

    async def wait_response(self, echo: str, timeout: float = 8.0):
        """等待 API 响应（配合 send_api 使用）"""
        fut = self._pending.get(echo)
        if not fut:
            fut = asyncio.get_event_loop().create_future()
            self._pending[echo] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(echo, None)

    async def call_api(self, action: str, params: dict = None,
                       timeout: float = 8.0) -> Optional[dict]:
        """调用 OneBot API 并等待结果（一步到位）"""
        echo = await self.send_api(action, params)
        return await self.wait_response(echo, timeout)

    async def reply(self, event: dict, text: str) -> bool:
        """回复消息（text 可以是纯文本或含 CQ 码的字符串）"""
        return await self.reply_raw(event, text)

    async def reply_raw(self, event: dict, message: str) -> bool:
        """发送原始消息（支持 CQ 码：表情、图片、戳一戳等）"""
        try:
            if event.get("message_type") == "group":
                await self.send_api("send_group_msg", {
                    "group_id": event.get("group_id"),
                    "message": message,
                })
            else:
                await self.send_api("send_private_msg", {
                    "user_id": event.get("user_id"),
                    "message": message,
                })
            return True
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False

    async def send_private(self, user_id: int | str, text: str):
        await self.send_api("send_private_msg", {"user_id": user_id, "message": text})

    async def send_group(self, group_id: int | str, text: str):
        await self.send_api("send_group_msg", {"group_id": group_id, "message": text})

    async def run_forever(self):
        """主循环：接收事件，带自动重连"""
        self._running = True
        while self._running:
            try:
                await self.connect()
                self.connected = True
                await self._event_loop()
            except websockets.ConnectionClosed as e:
                logger.warning(f"连接断开 (code={e.code})，{self.reconnect_delay}秒后重连...")
            except Exception as e:
                logger.error(f"连接异常: {e}，{self.reconnect_delay}秒后重连...")
            finally:
                self.connected = False
                await self._close()

            if self._running:
                await asyncio.sleep(self.reconnect_delay)

    async def _event_loop(self):
        """接收并分发事件"""
        async for raw in self.ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # API 响应：分发给等待的 Future
            if "echo" in data:
                fut = self._pending.get(data["echo"])
                if fut and not fut.done():
                    fut.set_result(data)
                continue

            post_type = data.get("post_type")

            if post_type == "meta_event":
                if data.get("meta_event_type") == "lifecycle":
                    self.self_id = str(data.get("self_id", ""))
                    logger.info(f"✅ 机器人上线，QQ={self.self_id}")
                continue

            if post_type == "message" and self._on_message:
                asyncio.create_task(self._safe_handle(data))

    async def _safe_handle(self, event: dict):
        try:
            await self._on_message(event)
        except Exception as e:
            logger.exception(f"处理消息异常: {e}")

    async def _close(self):
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def stop(self):
        self._running = False
        await self._close()
