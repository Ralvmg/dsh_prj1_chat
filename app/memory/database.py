"""数据库封装 —— SQLite 连接管理和表结构"""

import logging
import aiosqlite
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # WAL 模式：并发读写更好，掉电更安全
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        logger.info(f"数据库已连接: {self.db_path}")

    async def _create_tables(self):
        await self._db.executescript("""
        -- 对话历史：session_key 形如 "private:123456" / "group:789:123456"
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_key TEXT    NOT NULL,
            user_id     TEXT    NOT NULL,
            nickname    TEXT,
            role        TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
            content     TEXT    NOT NULL,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_key, id DESC);
        CREATE INDEX IF NOT EXISTS idx_messages_user
            ON messages(user_id, id DESC);

        -- 用户画像（长期记忆）
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id      TEXT PRIMARY KEY,
            nickname     TEXT,
            profile      TEXT DEFAULT '',
            msg_count    INTEGER DEFAULT 0,
            first_seen   INTEGER,
            last_seen    INTEGER,
            updated_at   INTEGER
        );

        -- 群设置
        CREATE TABLE IF NOT EXISTS group_settings (
            group_id    TEXT PRIMARY KEY,
            persona     TEXT DEFAULT '',
            enabled     INTEGER DEFAULT 1,
            reply_prob  REAL DEFAULT 1.0,
            created_at  INTEGER
        );

        -- 系统状态（当前人设等）
        CREATE TABLE IF NOT EXISTS system_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- 对话压缩摘要（长对话不失忆）
        CREATE TABLE IF NOT EXISTS conversation_summary (
            session_key TEXT PRIMARY KEY,
            summary     TEXT NOT NULL DEFAULT '',
            msg_count   INTEGER DEFAULT 0,
            updated_at  INTEGER
        );

        -- 学到的用户事实（离散条目，可累积、可纠正）
        CREATE TABLE IF NOT EXISTS user_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            fact       TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            source     TEXT DEFAULT '',
            created_at INTEGER,
            updated_at INTEGER,
            UNIQUE(user_id, fact)
        );
        CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id, confidence DESC);

        -- 学到的群内黑话/新词
        CREATE TABLE IF NOT EXISTS learned_terms (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            scope      TEXT DEFAULT 'global',   -- global / group:{gid}
            term       TEXT NOT NULL,
            meaning    TEXT NOT NULL,
            hits       INTEGER DEFAULT 1,
            created_at INTEGER,
            updated_at INTEGER,
            UNIQUE(scope, term)
        );
        CREATE INDEX IF NOT EXISTS idx_terms_scope ON learned_terms(scope, hits DESC);

        -- 学习/自检运行记录
        CREATE TABLE IF NOT EXISTS learn_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT,
            detail     TEXT,
            created_at INTEGER
        );

        -- 联网结果缓存
        CREATE TABLE IF NOT EXISTS web_cache (
            key        TEXT PRIMARY KEY,
            payload    TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );

        -- 知识库（机器人自己整理的长期知识）
        CREATE TABLE IF NOT EXISTS knowledge (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic      TEXT NOT NULL,
            content    TEXT NOT NULL,
            source     TEXT DEFAULT '',
            tags       TEXT DEFAULT '',
            hit_count  INTEGER DEFAULT 0,
            created_at INTEGER,
            updated_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge(topic);
        """)
        await self._db.commit()

    async def execute(self, sql: str, params: tuple = ()):
        cur = await self._db.execute(sql, params)
        await self._db.commit()
        return cur

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("数据库已关闭")
