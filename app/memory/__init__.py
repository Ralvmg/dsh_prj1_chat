"""记忆系统 —— 统一入口

对外只暴露 Memory 一个类，内部按职责拆分：
- ConversationMemory: 对话历史 + 压缩摘要（中期）
- ProfileMemory:      用户画像摘要（长期，概括式）
- FactsMemory:        用户事实条目 + 群内黑话（长期，原子式，可纠正）
- Database:           连接管理
"""

import logging
from .database import Database
from .conversation import ConversationMemory
from .profile import ProfileMemory
from .facts import FactsMemory

logger = logging.getLogger(__name__)


class Memory:
    def __init__(self, db_path: str):
        self.db = Database(db_path)
        self.conversation: ConversationMemory = None
        self.profile: ProfileMemory = None
        self.facts: FactsMemory = None

    async def connect(self):
        await self.db.connect()
        self.conversation = ConversationMemory(self.db)
        self.profile = ProfileMemory(self.db)
        self.facts = FactsMemory(self.db)
        logger.info("记忆系统已就绪（历史/画像/事实/黑话）")

    async def close(self):
        await self.db.close()

    async def cleanup(self, keep_days: int = 30, keep_per_session: int = 200):
        return await self.conversation.cleanup(keep_days, keep_per_session)
