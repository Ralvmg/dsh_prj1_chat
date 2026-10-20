"""学习引擎 —— 让机器人越聊越懂人、越聊越像群里的人

三个学习目标：
1. 用户事实：从对话里提取原子事实（职业、爱好、宠物、在忙什么…），累积存储
2. 群内黑话：从群聊里识别新词/缩写/梗，记下含义
3. 群说话风格：这个群是短句多还是长句多、爱用什么语气词

学习不是每句话都学（太贵），而是定时批量学：
- 每隔 LEARNING_INTERVAL_MIN 分钟跑一轮
- 每轮只处理"最近有消息、但还没学过"的会话
- 结果写进 user_facts / learned_terms / system_state
"""

import re
import json
import time
import logging
import asyncio

logger = logging.getLogger(__name__)


class LearningEngine:
    def __init__(self, memory, llm, interval_min: int = 30):
        self.memory = memory
        self.llm = llm
        self.interval = max(5, interval_min) * 60
        self._last_run = {}   # session_key -> timestamp

    # ---------- 主循环 ----------

    async def run_loop(self):
        logger.info(f"学习引擎已启动（每 {self.interval // 60} 分钟）")
        while True:
            try:
                await asyncio.sleep(self.interval)
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"学习轮次异常: {e}")

    async def run_once(self):
        await self.learn_user_facts()
        await self.learn_group_terms()

    # ---------- 1. 用户事实 ----------

    async def learn_user_facts(self):
        """挑最近活跃的用户，提取事实"""
        rows = await self.memory.db.fetchall(
            """SELECT user_id, nickname, MAX(created_at) AS last
               FROM messages WHERE role = 'user'
               GROUP BY user_id ORDER BY last DESC LIMIT 5"""
        )
        for r in rows:
            uid = r["user_id"]
            if time.time() - self._last_run.get(f"fact:{uid}", 0) < self.interval:
                continue
            self._last_run[f"fact:{uid}"] = time.time()

            history = await self.memory.conversation.get_history(f"private:{uid}", 40)
            if len(history) < 6:
                # 私聊不够就用群里的
                history = await self.memory.db.fetchall(
                    """SELECT role, content, nickname FROM messages
                       WHERE user_id = ? ORDER BY id DESC LIMIT 40""",
                    (uid,),
                )
                history = list(reversed(history))
            if len(history) < 6:
                continue

            convo = "\n".join(
                f"{'对方' if h['role'] == 'user' else '你'}: {h['content'][:200]}"
                for h in history
            )
            facts = await self._extract_facts(convo, r.get("nickname") or uid)
            if facts:
                for f in facts[:6]:
                    await self.memory.facts.add_fact(uid, f, confidence=0.7, source="auto")
                await self.memory.facts.log("facts", f"{r.get('nickname') or uid}: {len(facts)} 条")
                logger.info(f"📖 从 {r.get('nickname') or uid} 的对话学到 {len(facts)} 条事实")

    async def _extract_facts(self, convo: str, who: str) -> list[str]:
        prompt = f"""从下面的聊天里提取关于「对方」的**客观事实**。

要求：
- 只提取明确说过的、有长期价值的信息（职业、所在地、爱好、养的宠物、在做的项目、作息习惯、重要人际关系等）
- 不要提取临时情绪（"他今天很累"）和无法验证的推测
- 每条事实一句话，不超过 30 字，第三人称
- 输出 JSON 数组，例如 ["在杭州做后端开发", "养了一只叫豆豆的猫"]
- 没有可提取的就输出 []

聊天记录：
{convo[:2500]}

只输出 JSON 数组，不要其他文字。"""
        try:
            raw = await self.llm.chat([{"role": "user", "content": prompt}],
                                      temperature=0.2, max_tokens=400)
            if not raw:
                return []
            m = re.search(r"\[[\s\S]*\]", raw)
            if not m:
                return []
            data = json.loads(m.group(0))
            return [str(x).strip() for x in data if str(x).strip()][:8]
        except Exception as e:
            logger.debug(f"事实提取失败: {e}")
            return []

    # ---------- 2. 群内黑话 ----------

    async def learn_group_terms(self):
        rows = await self.memory.db.fetchall(
            """SELECT session_key FROM messages
               WHERE session_key LIKE 'group:%' GROUP BY session_key LIMIT 50"""
        )
        groups = set()
        for r in rows:
            parts = r["session_key"].split(":")
            if len(parts) >= 2:
                groups.add(parts[1])

        for gid in list(groups)[:3]:
            key = f"terms:{gid}"
            if time.time() - self._last_run.get(key, 0) < self.interval:
                continue
            self._last_run[key] = time.time()

            msgs = await self.memory.conversation.get_group_history(gid, 60)
            if len(msgs) < 20:
                continue

            texts = "\n".join(f"{m.get('nickname') or '某人'}: {m['content'][:120]}"
                              for m in msgs if m["role"] == "user")
            if len(texts) < 100:
                continue

            learned = await self._extract_terms(texts)
            for term, meaning in learned.items():
                await self.memory.facts.add_term(f"group:{gid}", term, meaning)
            if learned:
                await self.memory.facts.log("terms", f"群{gid}: {list(learned.keys())[:5]}")
                logger.info(f"📚 群 {gid} 学到 {len(learned)} 个词: {list(learned.keys())[:5]}")

    async def _extract_terms(self, group_text: str) -> dict:
        prompt = f"""从下面这段群聊里，找出**小圈子黑话/新词/缩写/梗**，解释它们的含义。

要求：
- 只找非通用词汇（例如特定游戏的简称、群友之间的称呼、网络新梗）
- 普通中文词、常见网络用语（"哈哈""牛逼"）不要
- 输出 JSON 对象，格式 {{"词": "含义（15字内）"}}
- 没有就输出 {{}}

群聊记录：
{group_text[:2500]}

只输出 JSON。"""
        try:
            raw = await self.llm.chat([{"role": "user", "content": prompt}],
                                      temperature=0.3, max_tokens=400)
            if not raw:
                return {}
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return {}
            data = json.loads(m.group(0))
            return {str(k): str(v) for k, v in data.items() if k and v}
        except Exception as e:
            logger.debug(f"黑话提取失败: {e}")
            return {}
