"""事实记忆 —— 可累积、可纠正的结构化长期记忆

跟"画像摘要"的区别：
- 画像摘要：一段话，覆盖式更新，容易丢细节
- 事实条目：一条一条的原子事实（"在杭州做后端"、"养了只叫豆豆的猫"），
  可单独增加/降低置信度/删除，检索时按置信度排序

这样机器人能"越聊越懂你"，而不是每次重新概括。
"""

import time
import logging

logger = logging.getLogger(__name__)


class FactsMemory:
    def __init__(self, db):
        self.db = db

    # ---------- 用户事实 ----------

    async def add_fact(self, user_id: str, fact: str, confidence: float = 0.8,
                       source: str = ""):
        """添加/强化一条事实"""
        now = int(time.time())
        await self.db.execute(
            """INSERT INTO user_facts (user_id, fact, confidence, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, fact) DO UPDATE SET
                   confidence = MIN(1.0, confidence + 0.1),
                   updated_at = excluded.updated_at""",
            (user_id, fact.strip(), confidence, source, now, now),
        )

    async def get_facts(self, user_id: str, limit: int = 8) -> list[str]:
        rows = await self.db.fetchall(
            """SELECT fact FROM user_facts WHERE user_id = ? AND confidence >= 0.3
               ORDER BY confidence DESC, updated_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return [r["fact"] for r in rows]

    async def weaken_fact(self, user_id: str, fact: str):
        """事实被纠正/推翻时降低置信度"""
        await self.db.execute(
            """UPDATE user_facts SET confidence = MAX(0, confidence - 0.5),
                   updated_at = ? WHERE user_id = ? AND fact = ?""",
            (int(time.time()), user_id, fact),
        )

    async def clear_all(self, user_id: str) -> int:
        cur = await self.db.execute("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
        return cur.rowcount

    # ---------- 群内黑话 ----------

    async def add_term(self, scope: str, term: str, meaning: str):
        now = int(time.time())
        await self.db.execute(
            """INSERT INTO learned_terms (scope, term, meaning, hits, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(scope, term) DO UPDATE SET
                   hits = hits + 1,
                   meaning = CASE WHEN excluded.meaning != '' THEN excluded.meaning ELSE meaning END,
                   updated_at = excluded.updated_at""",
            (scope, term.strip(), meaning.strip(), now, now),
        )

    async def get_terms(self, scope: str, limit: int = 10) -> dict[str, str]:
        rows = await self.db.fetchall(
            """SELECT term, meaning FROM learned_terms
               WHERE scope = 'global' OR scope = ?
               ORDER BY hits DESC LIMIT ?""",
            (scope, limit),
        )
        return {r["term"]: r["meaning"] for r in rows}

    # ---------- 学习日志 ----------

    async def log(self, kind: str, detail: str):
        await self.db.execute(
            "INSERT INTO learn_log (kind, detail, created_at) VALUES (?, ?, ?)",
            (kind, detail[:500], int(time.time())),
        )

    async def recent_logs(self, limit: int = 5) -> list[dict]:
        return await self.db.fetchall(
            "SELECT kind, detail, created_at FROM learn_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
