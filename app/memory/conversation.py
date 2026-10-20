"""对话历史管理 —— 短期上下文 + 群聊共享上下文 + 历史压缩摘要

三层记忆：
1. 短期：最近 N 条原始消息（进 prompt）
2. 压缩层：更早的对话被摘要成一段"更早的记忆"（进 prompt，省 token 不失忆）
3. 归档：完全过期的消息定期清理

群聊特殊性：
- 会话键用 group:{群号} 作为"群公共上下文"，能看到所有成员发言
- 同时保留 group:{群号}:{用户号} 作为"该用户的私聊线"（兼容旧数据）
"""

import time
import json
import logging
from .database import Database

logger = logging.getLogger(__name__)


class ConversationMemory:
    def __init__(self, db: Database):
        self.db = db

    # ---------- 写入 ----------

    async def add(self, session_key: str, user_id: str, nickname: str,
                  role: str, content: str):
        """添加一条消息"""
        await self.db.execute(
            """INSERT INTO messages (session_key, user_id, nickname, role, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_key, user_id, nickname, role, content, int(time.time())),
        )

    # ---------- 读取 ----------

    async def get_history(self, session_key: str, limit: int = 20) -> list[dict]:
        """获取某会话最近 N 条对话，按时间正序"""
        rows = await self.db.fetchall(
            """SELECT role, content, nickname FROM messages
               WHERE session_key = ?
               ORDER BY id DESC LIMIT ?""",
            (session_key, limit),
        )
        return list(reversed(rows))

    async def get_group_history(self, group_id: str, limit: int = 20) -> list[dict]:
        """获取群公共上下文 —— 所有成员在该群的发言（含昵称）

        这是群聊能"看懂气氛"的关键：能看到 A、B、C 都说了什么。
        """
        pattern = f"group:{group_id}:%"
        rows = await self.db.fetchall(
            """SELECT role, content, nickname FROM messages
               WHERE session_key LIKE ? OR session_key = ?
               ORDER BY id DESC LIMIT ?""",
            (pattern, f"group:{group_id}", limit),
        )
        return list(reversed(rows))

    async def count(self, session_key: str) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM messages WHERE session_key = ?", (session_key,)
        )
        return row["n"] if row else 0

    async def count_group(self, group_id: str) -> int:
        row = await self.db.fetchone(
            """SELECT COUNT(*) AS n FROM messages
               WHERE session_key LIKE ? OR session_key = ?""",
            (f"group:{group_id}:%", f"group:{group_id}"),
        )
        return row["n"] if row else 0

    # ---------- 压缩摘要 ----------

    async def get_summary(self, session_key: str) -> str:
        row = await self.db.fetchone(
            "SELECT summary FROM conversation_summary WHERE session_key = ?",
            (session_key,),
        )
        return row["summary"] if row and row["summary"] else ""

    async def set_summary(self, session_key: str, summary: str):
        await self.db.execute(
            """INSERT INTO conversation_summary (session_key, summary, updated_at, msg_count)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_key) DO UPDATE SET
                   summary = excluded.summary,
                   updated_at = excluded.updated_at,
                   msg_count = excluded.msg_count""",
            (session_key, summary, int(time.time()),
             await self.count(session_key)),
        )

    async def get_unsummarized(self, session_key: str, keep_recent: int = 20) -> list[dict]:
        """拿到"还没进摘要、但已经要滚出短期窗口"的那部分消息（私聊）"""
        total = await self.count(session_key)
        if total <= keep_recent:
            return []
        offset = total - keep_recent
        rows = await self.db.fetchall(
            """SELECT role, content, nickname, created_at FROM messages
               WHERE session_key = ? ORDER BY id ASC LIMIT ?""",
            (session_key, offset),
        )
        return rows

    async def get_group_unsummarized(self, group_id: str, keep_recent: int = 20) -> list[dict]:
        """群聊版：把该群所有成员的旧消息聚合起来"""
        total = await self.count_group(group_id)
        if total <= keep_recent:
            return []
        offset = total - keep_recent
        rows = await self.db.fetchall(
            """SELECT role, content, nickname, created_at FROM messages
               WHERE session_key LIKE ? OR session_key = ?
               ORDER BY id ASC LIMIT ?""",
            (f"group:{group_id}:%", f"group:{group_id}", offset),
        )
        return rows

    # ---------- 清理 ----------

    async def clear(self, session_key: str) -> int:
        """清空某个会话的历史，返回删除条数"""
        cur = await self.db.execute(
            "DELETE FROM messages WHERE session_key = ?", (session_key,)
        )
        await self.db.execute(
            "DELETE FROM conversation_summary WHERE session_key = ?", (session_key,)
        )
        return cur.rowcount

    async def cleanup(self, keep_days: int = 30, keep_per_session: int = 200):
        """清理旧数据"""
        cutoff = int(time.time()) - keep_days * 86400

        cur = await self.db.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        deleted_old = cur.rowcount

        cur = await self.db.execute("""
            DELETE FROM messages WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY session_key ORDER BY id DESC
                    ) AS rn FROM messages
                ) WHERE rn <= ?
            )
        """, (keep_per_session,))
        deleted_extra = cur.rowcount

        if deleted_old or deleted_extra:
            logger.info(f"清理完成: 超期 {deleted_old} 条, 超量 {deleted_extra} 条")
        return deleted_old + deleted_extra
