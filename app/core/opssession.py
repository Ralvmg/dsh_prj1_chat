"""服务器操作会话模式

交互设计（防误触发）：
- 入口：管理员在私聊里发 `/help me for linux by ra 666` → 进入操作模式
- 模式内：每条消息都当 bash 命令执行（无需任何前缀）
- 自动退出：60 秒内没有任何操作
- 手动退出：发 `/exit help`
- 只有白名单管理员、只在私聊生效；群聊和其它人完全看不到这个入口

状态只在内存里，进程重启即失效（更安全）。
"""

import asyncio
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class OpsSession:
    """单个管理员的服务器操作会话"""

    def __init__(self, user_id: str, ops, send_func: Callable, idle_seconds: int,
                 exit_phrase: str):
        self.user_id = user_id
        self.ops = ops
        self.send = send_func          # async (user_id, text) -> None，用于超时通知
        self.idle_seconds = idle_seconds
        self.exit_phrase = exit_phrase
        self.active = True
        self.last_active = time.time()
        self._timer: Optional[asyncio.Task] = None

    def touch(self):
        self.last_active = time.time()
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._idle_watch())

    async def _idle_watch(self):
        try:
            await asyncio.sleep(self.idle_seconds)
            # 再确认一次是否真的空闲
            if self.active and time.time() - self.last_active >= self.idle_seconds - 1:
                self.active = False
                logger.info(f"⏱️ 服务器操作会话超时退出: {self.user_id}")
                try:
                    await self.send(self.user_id,
                                    f"（{self.idle_seconds} 秒没有操作，已退出服务器操作模式。\n"
                                    f"要再进来发：/help me for linux by ra 666）")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    def close(self):
        self.active = False
        if self._timer and not self._timer.done():
            self._timer.cancel()


class OpsSessionManager:
    def __init__(self, ops, send_func: Callable, admins: list[str],
                 entry_phrase: str, exit_phrase: str, idle_seconds: int = 60):
        self.ops = ops
        self.send = send_func
        self.admins = set(admins)
        self.entry_phrase = entry_phrase.strip()
        self.exit_phrase = exit_phrase.strip()
        self.idle_seconds = idle_seconds
        self._sessions: dict[str, OpsSession] = {}

    def _is_admin(self, user_id: str) -> bool:
        return user_id in self.admins

    async def handle(self, text: str, user_id: str, is_private: bool) -> Optional[str]:
        """处理一条消息；返回要回复的文本，None 表示不归我管（走正常聊天）"""
        if not self.ops or not is_private or not self._is_admin(user_id):
            return None

        msg = text.strip()

        # 入口
        if msg == self.entry_phrase:
            sess = self._sessions.get(user_id)
            if sess and sess.active:
                sess.touch()
                return "服务器操作模式已经在开了，直接发命令。发 /exit help 退出。"
            sess = OpsSession(user_id, self.ops, self.send,
                              self.idle_seconds, self.exit_phrase)
            self._sessions[user_id] = sess
            sess.touch()
            logger.info(f"🔓 管理员进入服务器操作模式: {user_id}")
            return (
                "🔓 服务器操作模式已开启（仅你可见）\n"
                f"· 直接发 bash 命令，我会执行并回结果\n"
                f"· {self.idle_seconds} 秒无操作自动退出\n"
                f"· 手动退出发：{self.exit_phrase}\n"
                "试试：df -h / docker ps"
            )

        # 退出
        if msg == self.exit_phrase:
            sess = self._sessions.get(user_id)
            if sess:
                sess.close()
                self._sessions.pop(user_id, None)
                return "已退出服务器操作模式。"
            return None

        # 模式内执行
        sess = self._sessions.get(user_id)
        if not sess or not sess.active:
            return None
        if time.time() - sess.last_active >= self.idle_seconds:
            sess.close()
            self._sessions.pop(user_id, None)
            return None  # 已过期，按普通聊天处理

        if not msg:
            return None

        # 兼容旧前缀
        cmd = msg[len("!!"):].strip() if msg.startswith("!!") else msg
        if not cmd:
            return None

        sess.touch()
        logger.info(f"🖥️ [会话] {user_id} 执行: {cmd[:150]}")
        try:
            await self.send(user_id, f"⏳ 执行中：{cmd[:60]}")
        except Exception:
            pass
        result = await self.ops.run_and_format(cmd)
        if len(result) > 1800:
            result = result[:1800] + "\n…（输出过长已截断）"
        return result

    def close_all(self):
        for s in self._sessions.values():
            s.close()
        self._sessions.clear()
