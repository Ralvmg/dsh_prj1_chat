"""用户画像管理 —— 长期记忆，跨会话保留"""

import time
import logging
from .database import Database

logger = logging.getLogger(__name__)


class ProfileMemory:
    def __init__(self, db: Database):
        self.db = db

    async def touch(self, user_id: str, nickname: str):
        """更新用户活跃信息（每次收到消息调用）"""
        now = int(time.time())
        await self.db.execute(
            """INSERT INTO user_profile (user_id, nickname, msg_count, first_seen, last_seen)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   nickname   = excluded.nickname,
                   msg_count  = msg_count + 1,
                   last_seen  = excluded.last_seen""",
            (user_id, nickname, now, now),
        )

    async def get(self, user_id: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
        )

    async def get_profile_text(self, user_id: str) -> str:
        row = await self.get(user_id)
        return row["profile"] if row and row["profile"] else ""

    async def set_profile_text(self, user_id: str, text: str):
        await self.db.execute(
            "UPDATE user_profile SET profile = ?, updated_at = ? WHERE user_id = ?",
            (text, int(time.time()), user_id),
        )

    async def msg_count(self, user_id: str) -> int:
        row = await self.get(user_id)
        return row["msg_count"] if row else 0

    async def should_update_profile(self, user_id: str, interval: int = 10) -> bool:
        """判断是否该更新画像（消息数达到间隔）"""
        n = await self.msg_count(user_id)
        return n > 0 and n % interval == 0

    async def top_users(self, limit: int = 10) -> list[dict]:
        """最活跃的用户"""
        return await self.db.fetchall(
            """SELECT user_id, nickname, msg_count, last_seen
               FROM user_profile ORDER BY msg_count DESC LIMIT ?""",
            (limit,),
        )
