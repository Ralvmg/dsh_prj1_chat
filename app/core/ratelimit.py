"""限流与 Token 保护 —— 防止短时大量聊天把 API 额度烧光

三层保护：
1. 突发合并：同一会话 2-3 秒内的多条消息合并成一次 LLM 调用（真人也是这样，一口气发三条）
2. 频率限制：每会话每分钟/每小时最多回复多少次；全局每分钟上限
3. 日预算：按字符数估算 token 消耗，超过当日预算就只回一句"今天话太多了"并静默

命令（/xxx）不计入限制，也不消耗 LLM。
"""

import time
import logging
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, per_min: int = 6, per_hour: int = 60,
                 global_per_min: int = 20, daily_char_budget: int = 200000):
        self.per_min = per_min
        self.per_hour = per_hour
        self.global_per_min = global_per_min
        self.daily_char_budget = daily_char_budget

        self._session_min: dict[str, deque] = defaultdict(deque)
        self._session_hour: dict[str, deque] = defaultdict(deque)
        self._global_min: deque = deque()

        self._day = time.strftime("%Y-%m-%d")
        self._day_chars = 0
        self._budget_warned = False

    # ---------- 时间窗口清理 ----------

    @staticmethod
    def _trim(dq: deque, window: float, now: float):
        while dq and now - dq[0] > window:
            dq.popleft()

    # ---------- 允许判断 ----------

    def allow(self, session_key: str) -> tuple[bool, str]:
        now = time.time()

        # 跨天重置
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._day_chars = 0
            self._budget_warned = False

        # 日预算
        if self._day_chars >= self.daily_char_budget:
            if not self._budget_warned:
                self._budget_warned = True
                logger.warning(f"⚠️ 今日 token 预算已用尽（{self._day_chars} 字符）")
            return False, "daily_budget"

        # 全局每分钟
        self._trim(self._global_min, 60, now)
        if len(self._global_min) >= self.global_per_min:
            return False, "global_rate"

        # 会话每分钟
        dq_min = self._session_min[session_key]
        self._trim(dq_min, 60, now)
        if len(dq_min) >= self.per_min:
            return False, "session_rate"

        # 会话每小时
        dq_hour = self._session_hour[session_key]
        self._trim(dq_hour, 3600, now)
        if len(dq_hour) >= self.per_hour:
            return False, "session_hour"

        return True, "ok"

    def record(self, session_key: str, reply_chars: int = 0, prompt_chars: int = 0):
        """记录一次成功回复（消耗）"""
        now = time.time()
        self._session_min[session_key].append(now)
        self._session_hour[session_key].append(now)
        self._global_min.append(now)
        # 粗略估算：输入+输出字符数 ≈ token 的 0.6-1 倍，这里按字符直接记
        self._day_chars += max(0, reply_chars) + max(0, prompt_chars)

    # ---------- 状态 ----------

    def stats(self) -> dict:
        now = time.time()
        self._trim(self._global_min, 60, now)
        return {
            "day": self._day,
            "day_chars": self._day_chars,
            "budget": self.daily_char_budget,
            "budget_left_pct": round(
                max(0, 1 - self._day_chars / max(1, self.daily_char_budget)) * 100, 1),
            "global_last_min": len(self._global_min),
        }


class MergeBuffer:
    """突发消息合并 —— 同一会话连续发消息时，攒一小会儿再一起处理

    真人也这样：别人一口气发三条"在吗""我有个事""问你一下"，
    你不会分三次回答，而是等对方说完一次答。
    """

    def __init__(self, window: float = 2.5):
        self.window = window
        self._pending: dict[str, dict] = {}

    def push(self, session_key: str, text: str, event: dict) -> bool:
        """把消息塞进缓冲；返回 True 表示这是新的一批（调用方该起定时器）"""
        item = self._pending.get(session_key)
        if item is None:
            self._pending[session_key] = {"texts": [text], "event": event, "ts": time.time()}
            return True
        item["texts"].append(text)
        item["event"] = event
        item["ts"] = time.time()
        return False

    def take(self, session_key: str) -> dict | None:
        """取走并合并缓冲"""
        item = self._pending.pop(session_key, None)
        if not item:
            return None
        return {"text": "\n".join(t for t in item["texts"] if t), "event": item["event"]}
