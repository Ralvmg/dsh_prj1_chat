"""主动行为引擎 —— 让机器人像真人一样主动找人说话

设计理念（借鉴 MaiBot 的"在合适的时候开口"）：

1. 主动问候：好久没聊的用户，随机时机主动发消息
2. 话题延续：对话进行到一半被打断，之后可能追问
3. 群聊插嘴：群里有讨论时小概率加入（严格限频）
4. 时间感知：早安/晚安/深夜提醒，带随机性不像定时任务

关键约束（防止骚扰）：
- 每个用户有主动消息冷却时间
- 全局每小时主动消息上限
- 深夜（0-7点）几乎不主动
- 用户可以发"别理我"关闭主动消息
"""

import asyncio
import os
import time
import random
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ProactiveEngine:
    def __init__(self, memory, llm, persona_manager, mood, onebot_client=None):
        self.memory = memory
        self.llm = llm
        self.persona_manager = persona_manager
        self.mood = mood
        self.client = onebot_client  # 稍后注入

        # ---- 配置 ----
        # 主动问候：用户沉默超过 N 小时后可能主动联系
        self.greeting_after_hours = float(os.getenv("PROACTIVE_GREETING_HOURS", "6"))
        # 话题延续：对话中断超过 N 分钟后可能追问
        self.followup_after_min = float(os.getenv("PROACTIVE_FOLLOWUP_MIN", "20"))
        # 主动问候冷却（同一用户两次主动消息的最小间隔，秒）
        self.cooldown_per_user = float(os.getenv("PROACTIVE_COOLDOWN", "3600"))
        # 全局每小时主动消息上限
        self.hourly_limit = int(os.getenv("PROACTIVE_HOURLY_LIMIT", "6"))
        # 群聊插嘴概率
        self.group_interrupt_prob = float(os.getenv("PROACTIVE_GROUP_PROB", "0.08"))
        # 群聊插嘴冷却
        self.group_cooldown = float(os.getenv("PROACTIVE_GROUP_COOLDOWN", "1800"))
        # 免打扰时段（不主动）：0-7 点
        self.quiet_hours = range(0, 7)

        # ---- 运行时状态 ----
        self._last_hour_sent = {}      # (year,month,day,hour) -> count
        self._last_group_interrupt = 0.0
        self._group_msg_buffer = {}    # group_id -> [(time, nickname, text)]
        self._opt_out_users = set()    # 用户说"别理我"后加入

    # ---------- 工具 ----------

    def _in_quiet_hours(self) -> bool:
        h = time.localtime().tm_hour
        return h in self.quiet_hours

    def _hour_key(self) -> tuple:
        now = time.localtime()
        return (now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour)

    def _hourly_count(self) -> int:
        return self._last_hour_sent.get(self._hour_key(), 0)

    def _can_send(self) -> bool:
        """全局发送限制检查"""
        if self._in_quiet_hours():
            return False
        return self._hourly_count() < self.hourly_limit

    def _record_send(self):
        k = self._hour_key()
        self._last_hour_sent[k] = self._last_hour_sent.get(k, 0) + 1
        # 清理旧 key
        if len(self._last_hour_sent) > 48:
            for key in list(self._last_hour_sent.keys()):
                if key != k:
                    del self._last_hour_sent[key]

    # ---------- 1. 主动问候 ----------

    async def check_greetings(self):
        """定期检查：哪些用户值得主动问候"""
        if not self._can_send():
            return
        if not self.mood.should_initiate():
            return

        now = time.time()
        threshold = now - self.greeting_after_hours * 3600

        # 找出聊过天但现在沉默很久的用户
        rows = await self.memory.db.fetchall(
            """SELECT user_id, nickname, MAX(created_at) AS last_seen, COUNT(*) AS n
               FROM messages WHERE role = 'user'
               GROUP BY user_id HAVING last_seen < ? AND n >= 3
               ORDER BY last_seen DESC LIMIT 5""",
            (threshold,),
        )
        if not rows:
            return

        # 随机挑一个（越久没聊权重越高）
        candidates = []
        for r in rows:
            uid = r["user_id"]
            if uid in self._opt_out_users:
                continue
            # 冷却检查
            last_sent = await self._get_last_proactive(uid)
            if now - last_sent < self.cooldown_per_user:
                continue
            hours_silent = (now - r["last_seen"]) / 3600
            candidates.append((uid, r["nickname"], hours_silent))

        if not candidates:
            return

        # 按沉默时长加权随机
        weights = [c[2] for c in candidates]
        uid, nickname, hours = random.choices(candidates, weights=weights, k=1)[0]

        if random.random() > 0.5:  # 即使符合条件也只发一半的概率，更自然
            return

        await self._send_greeting(uid, nickname, hours)

    async def _get_last_proactive(self, user_id: str) -> float:
        row = await self.memory.db.fetchone(
            "SELECT value FROM system_state WHERE key = ?", (f"proactive_{user_id}",)
        )
        return float(row["value"]) if row else 0.0

    async def _set_last_proactive(self, user_id: str):
        await self.memory.db.execute(
            """INSERT INTO system_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (f"proactive_{user_id}", str(time.time())),
        )

    async def _send_greeting(self, user_id: str, nickname: str, hours_silent: float):
        """生成并发送主动问候"""
        persona = self.persona_manager.current
        mood_hint = self.mood.prompt_hint()
        time_str = datetime.now().strftime("%H:%M")

        # 取一段历史，让问候有上下文
        history = await self.memory.conversation.get_history(f"private:{user_id}", 5)
        history_text = "\n".join(
            f"{'对方' if h['role']=='user' else '你'}: {h['content']}" for h in history
        ) if history else "（之前聊过，但没有具体记录）"

        prompt = f"""你现在想主动给 {nickname} 发一条 QQ 消息。

背景：
- 你们已经 {hours_silent:.0f} 小时没聊天了
- 之前聊过这些：{history_text[:500]}
- 现在时间 {time_str}
- {mood_hint}

要求：
1. 只输出你要发送的那一条消息，不要任何解释
2. 像真人突然想起一个人那样自然，可以问候、可以分享、可以吐槽
3. 要和之前聊过的内容有关联（比如之前聊过游戏，可以问"你那个游戏打到哪了"）
4. 短，一两句话
5. 符合你的人设语气
6. 不要用"在吗"这种很尬的开场"""

        reply = await self.llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=1.0,
            max_tokens=150,
        )
        if not reply:
            return

        await self.client.send_private(user_id, reply)
        await self._set_last_proactive(user_id)
        self._record_send()
        await self.memory.conversation.add(
            f"private:{user_id}", user_id, persona.name, "assistant", reply
        )
        logger.info(f"🎯 主动问候 {nickname}({user_id}): {reply[:40]}")

    # ---------- 2. 话题延续 ----------

    async def check_followups(self):
        """检查进行到一半的对话，可能追问"""
        if not self._can_send():
            return

        now = time.time()
        cutoff = now - self.followup_after_min * 60

        # 找最近有对话、但最后一条是用户发的（说明机器人回完用户没接）
        # 且间隔超过 followup_after_min 的会话
        rows = await self.memory.db.fetchall(
            """SELECT session_key, user_id, nickname, MAX(created_at) AS last_time
               FROM messages
               WHERE created_at < ? AND created_at > ?
               GROUP BY session_key""",
            (now - 60, now - self.followup_after_min * 60),
        )
        if not rows:
            return

        for r in rows[:3]:
            if not r["session_key"].startswith("private:"):
                continue
            uid = r["user_id"]
            if uid in self._opt_out_users:
                continue

            # 检查最后一条是谁发的
            history = await self.memory.conversation.get_history(r["session_key"], 2)
            if not history or history[-1]["role"] != "assistant":
                continue  # 最后一跳是用户，不需要追问

            # 太久没聊了就不追（超过 3 小时算过气话题）
            if now - r["last_time"] > 3 * 3600:
                continue

            last_sent = await self._get_last_proactive(uid)
            if now - last_sent < self.cooldown_per_user / 2:
                continue

            if random.random() > 0.35:
                continue

            await self._send_followup(uid, r["nickname"], history)

    async def _send_followup(self, user_id: str, nickname: str, history: list):
        persona = self.persona_manager.current
        convo = "\n".join(
            f"{'对方' if h['role']=='user' else '你'}: {h['content']}" for h in history
        )
        mood_hint = self.mood.prompt_hint()

        prompt = f"""你和 {nickname} 刚才在聊天，聊到一半对方没回你。

刚才的对话：
{convo[:600]}
{mood_hint}

要求：
1. 只输出要发送的一条消息
2. 可以追问刚才的话题，也可以想到什么说什么，或者自然地转移话题
3. 短，一句话就好
4. 符合人设，别用"我们刚才说到"这种生硬表达"""

        reply = await self.llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=1.0, max_tokens=120,
        )
        if not reply:
            return

        await self.client.send_private(user_id, reply)
        await self._set_last_proactive(user_id)
        self._record_send()
        await self.memory.conversation.add(
            f"private:{user_id}", user_id, persona.name, "assistant", reply
        )
        logger.info(f"💬 话题延续 {nickname}({user_id}): {reply[:40]}")

    # ---------- 3. 群聊插嘴 ----------

    async def observe_group_message(self, event: dict):
        """观察群消息（所有群消息都进 buffer，不管是否触发回复）"""
        if event.get("message_type") != "group":
            return
        gid = str(event.get("group_id"))
        nickname = (event.get("sender", {}) or {}).get("card") or str(event.get("user_id"))
        text = event.get("raw_message", "")
        if not text or len(text) < 4:
            return

        buf = self._group_msg_buffer.setdefault(gid, [])
        buf.append((time.time(), nickname, text))

        # 只保留最近 10 分钟
        cutoff = time.time() - 600
        self._group_msg_buffer[gid] = [m for m in buf if m[0] > cutoff]

    async def check_group_interrupt(self):
        """群聊氛围感知：小概率插嘴"""
        if not self._can_send():
            return
        now = time.time()
        if now - self._last_group_interrupt < self.group_cooldown:
            return

        for gid, buf in list(self._group_msg_buffer.items()):
            if len(buf) < 4:  # 至少 4 条消息才算有讨论
                continue
            if random.random() > self.group_interrupt_prob:
                continue

            # 取最近几条
            recent = buf[-5:]
            convo = "\n".join(f"{n}: {t}" for _, n, t in recent)
            mood_hint = self.mood.prompt_hint()

            prompt = f"""你在 QQ 群里潜水，最近群里在聊：

{convo[:800]}
{mood_hint}

如果你对这个话题感兴趣，可以说一句；不感兴趣就输出 SKIP。
要求：只输出要发送的一条消息或 SKIP。要口语化，一两句，符合人设。"""

            reply = await self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=1.0, max_tokens=150,
            )
            if not reply or "SKIP" in reply.upper()[:10]:
                continue

            await self.client.send_group(gid, reply)
            self._last_group_interrupt = now
            self._record_send()
            self._group_msg_buffer[gid] = []  # 清空，防止连续插嘴
            logger.info(f"🗣️ 群聊插嘴 {gid}: {reply[:40]}")
            return  # 一次只插一个群

    # ---------- 4. 时间感知行为 ----------

    async def check_time_based(self):
        """早安/晚安等时间行为（低频、随机）"""
        if not self._can_send():
            return
        now = time.localtime()
        h = now.tm_hour

        # 早上 8-10 点：对昨天聊得很晚的人说早安
        if 8 <= h < 10 and random.random() < 0.15:
            rows = await self.memory.db.fetchall(
                """SELECT user_id, nickname FROM messages
                   WHERE role='user' AND created_at > ?
                   GROUP BY user_id ORDER BY MAX(created_at) DESC LIMIT 3""",
                (time.time() - 86400,),
            )
            for r in rows:
                uid = str(r["user_id"])
                if uid in self._opt_out_users:
                    continue
                # 检查今天是否已经问候过
                key = f"greet_{uid}_{now.tm_mday}"
                row = await self.memory.db.fetchone(
                    "SELECT value FROM system_state WHERE key = ?", (key,)
                )
                if row:
                    continue
                if random.random() > 0.4:
                    continue

                persona = self.persona_manager.current
                mood_hint = self.mood.prompt_hint()
                reply = await self.llm.chat(
                    [{"role": "user", "content":
                      f"早上刚醒，想给 {r['nickname']} 发句早安（也可以不是早安，随意点）。\n{mood_hint}\n只输出消息本身，一句话，符合人设。"}],
                    temperature=1.0, max_tokens=80,
                )
                if reply:
                    await self.client.send_private(uid, reply)
                    await self.memory.db.execute(
                        """INSERT INTO system_state (key, value) VALUES (?, '1')
                           ON CONFLICT(key) DO UPDATE SET value='1'""", (key,),
                    )
                    self._record_send()
                    await self.memory.conversation.add(
                        f"private:{uid}", uid, persona.name, "assistant", reply
                    )
                    logger.info(f"🌅 时间问候 {r['nickname']}: {reply[:30]}")
                return  # 一次只发一个

    # ---------- 免打扰 ----------

    def handle_opt_out(self, text: str, user_id: str) -> bool:
        """用户说别理我，关闭主动消息"""
        patterns = ["别理我", "不要理我", "别找我", "安静点", "别烦我", "别主动找我"]
        if any(p in text for p in patterns):
            self._opt_out_users.add(user_id)
            logger.info(f"用户 {user_id} 已关闭主动消息")
            return True
        # 恢复
        if any(p in text for p in ["可以理我了", "来找我玩", "理我吧"]):
            self._opt_out_users.discard(user_id)
            logger.info(f"用户 {user_id} 恢复主动消息")
            return True
        return False

    # ---------- 主循环 ----------

    async def run_loop(self):
        """主动行为的定时检查循环"""
        logger.info("主动行为引擎已启动")
        while True:
            try:
                await self.mood.normalize()
                await self.check_greetings()
                await self.check_followups()
                await self.check_group_interrupt()
                await self.check_time_based()
            except Exception as e:
                logger.exception(f"主动行为引擎异常: {e}")
            await asyncio.sleep(random.uniform(120, 240))  # 每 2-4 分钟检查一次
