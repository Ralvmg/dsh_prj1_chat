"""心情状态机 —— 让机器人有持续的情绪状态

设计：
- 每个心情有能量值 (energy 0-100) 和好感度倾向
- 心情随时间自然衰减/恢复
- 事件（被夸、被怼、聊天中断）会影响心情
- 心情影响说话风格（在 prompt 里注入当前状态）

心情种类：
- energetic  元气满满（早上、被夸后）
- happy      开心（正常聊天）
- neutral    平静（默认）
- bored      无聊（很久没人理）
- sleepy     困（深夜）
- annoyed    有点生气（被怼）
"""

import time
import random
import logging

logger = logging.getLogger(__name__)

# 心情定义: (能量基础, 描述)
MOODS = {
    "energetic": {"energy": 90, "desc": "元气满满，尾巴摇得飞起"},
    "happy":     {"energy": 75, "desc": "开心，想找人聊天"},
    "neutral":   {"energy": 60, "desc": "平静，懒洋洋地飘着"},
    "bored":     {"energy": 35, "desc": "无聊，尾巴有一搭没一搭地晃"},
    "sleepy":    {"energy": 20, "desc": "困，眼皮打架，说话都慢半拍"},
    "annoyed":   {"energy": 70, "desc": "有点生气，鼓着腮帮子喷水"},
}

# 事件对心情的影响
EVENT_EFFECTS = {
    "praised":     +15,   # 被夸
    "insulted":    -20,   # 被怼/说胖
    "good_chat":   +8,    # 愉快的对话
    "ignored":     -3,    # 被冷落
    "long_break":  -10,   # 长时间没人说话
}

# 时段基础心情
HOUR_MOOD = {
    # 0-5 深夜: sleepy, 6-10 早上: energetic, 11-14 中午: happy,
    # 15-18 下午: neutral, 19-23 晚上: happy
    range(0, 6):   "sleepy",
    range(6, 11):  "energetic",
    range(11, 15): "happy",
    range(15, 19): "neutral",
    range(19, 24): "happy",
}


class MoodEngine:
    def __init__(self, db):
        self.db = db
        self._mood = "neutral"
        self._energy = 60.0
        self._last_event_time = time.time()

    async def load(self):
        """从数据库恢复心情状态"""
        row = await self.db.fetchone(
            "SELECT value FROM system_state WHERE key = 'mood_state'"
        )
        if row:
            try:
                import json
                data = json.loads(row["value"])
                self._mood = data.get("mood", "neutral")
                self._energy = float(data.get("energy", 60))
                self._last_event_time = float(data.get("last_event", time.time()))
                logger.info(f"已恢复心情状态: {self._mood} (能量 {self._energy:.0f})")
            except Exception as e:
                logger.warning(f"恢复心情失败: {e}")
        await self.normalize()

    async def save(self):
        import json
        await self.db.execute(
            """INSERT INTO system_state (key, value) VALUES ('mood_state', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (json.dumps({
                "mood": self._mood,
                "energy": self._energy,
                "last_event": self._last_event_time,
            }),),
        )

    async def normalize(self):
        """自然衰减 + 时段修正，把心情拉回合理区间"""
        now = time.time()

        # 长时间没有互动，能量自然下降
        idle_hours = (now - self._last_event_time) / 3600
        if idle_hours > 1:
            self._energy -= min(idle_hours * 5, 30)

        # 时段基础心情
        hour = time.localtime().tm_hour
        base_mood = "neutral"
        for rng, m in HOUR_MOOD.items():
            if hour in rng:
                base_mood = m
                break

        # 能量恢复：白天慢慢回
        if 7 <= hour <= 23:
            self._energy = min(100, self._energy + 10)
        else:
            self._energy = max(10, self._energy - 5)

        # 根据能量修正心情（除非处于 annoy 这种强状态）
        if self._mood != "annoyed":
            if self._energy >= 85:
                self._mood = "energetic"
            elif self._energy >= 65:
                self._mood = "happy" if base_mood in ("happy", "energetic") else "neutral"
            elif self._energy >= 40:
                self._mood = "neutral" if base_mood != "sleepy" else "sleepy"
            elif self._energy >= 25:
                self._mood = "bored"
            else:
                self._mood = "sleepy"

        self._last_event_time = now
        await self.save()

    async def trigger(self, event: str):
        """触发心情事件"""
        delta = EVENT_EFFECTS.get(event, 0)
        self._energy = max(5, min(100, self._energy + delta))
        self._last_event_time = time.time()

        # 特殊事件直接改变心情
        if event == "insulted":
            self._mood = "annoyed"
        elif event == "praised" and self._energy > 80:
            self._mood = "energetic"

        logger.info(f"心情事件 {event}: 能量{delta:+d} → {self._mood} ({self._energy:.0f})")
        await self.save()

    @property
    def current(self) -> str:
        return self._mood

    @property
    def energy(self) -> float:
        return self._energy

    def describe(self) -> str:
        """当前心情描述（注入 prompt 用）"""
        info = MOODS.get(self._mood, MOODS["neutral"])
        return f"你现在的心情: {self._mood}（{info['desc']}，能量 {self._energy:.0f}/100）"

    def prompt_hint(self) -> str:
        """心情作为状态事实注入（不规定该怎么表现）"""
        info = MOODS.get(self._mood, MOODS["neutral"])
        return f"{self._mood}（{info['desc']}）"

    # ---------- 心情 → 人格自动切换 ----------

    def persona_for_mood(self) -> str:
        """根据心情决定用哪个人格

        - 生气/被惹 → 哦鲸鲸（嘴硬带刺）
        - 困/无聊   → 小鲸鲸（软糯安静）
        - 高兴/元气 → 大肥鱼（话多爱笑）
        """
        if self._mood in ("annoyed", "angry", "insulted"):
            return "ojingjing"
        if self._mood in ("sleepy", "bored"):
            return "xiaojingjing"
        return "dafeiyu"

    def should_initiate(self) -> bool:
        """当前状态是否适合主动找人说话"""
        # 无聊或元气满满时更想找人聊，困的时候不想
        if self._mood in ("bored", "energetic"):
            return random.random() < 0.7
        if self._mood == "happy":
            return random.random() < 0.4
        if self._mood == "sleepy":
            return random.random() < 0.05
        return random.random() < 0.2
