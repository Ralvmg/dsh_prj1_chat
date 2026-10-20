"""拟人化回复策略 —— 像真人那样一条条发短消息

真人发 QQ 的特征：
1. 不会一次发一大段，而是拆成几条短消息
2. 每条之间有小间隔（思考/打字）
3. 短句优先（"哦" "还真是" 这种也算一条）

参照 AstrBot 的做法：把长回复按句拆成多条，逐条发送。
"""

import asyncio
import random
import re
import logging

logger = logging.getLogger(__name__)


class ReplyStrategy:
    def __init__(self, delay_min: float = 0.2, delay_max: float = 0.9,
                 split_enabled: bool = True, split_threshold: int = 40):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.split_enabled = split_enabled
        self.split_threshold = split_threshold

    async def think_delay(self, text: str = ""):
        """模拟思考延迟（很短，配合上游合并窗口）"""
        base = random.uniform(self.delay_min, self.delay_max)
        extra = min(len(text) / 200, 0.8) if text else 0
        await asyncio.sleep(base + extra)

    # ---------- 拆分 ----------

    def split(self, text: str) -> list[str]:
        """把回复拆成多条短消息

        - CQ 码（表情/图片）不会被切断
        - 优先按换行拆，其次按句号/问号/感叹号/省略号拆
        - 太短的句子会合并，但阈值很小（40 字），保证多条发送
        """
        if not text:
            return []
        if not self.split_enabled:
            return [text]

        # 保护 CQ 码
        placeholders = {}
        protected = text
        for i, m in enumerate(re.finditer(r"\[CQ:[^\]]+\]", text)):
            ph = f"\x00C{i}\x00"
            placeholders[ph] = m.group(0)
            protected = protected.replace(m.group(0), ph, 1)

        parts = self._split_plain(protected)

        # 还原 CQ 码
        out = []
        for p in parts:
            for ph, cq in placeholders.items():
                p = p.replace(ph, cq)
            p = p.strip()
            if p:
                out.append(p)
        return out or [text]

    def _split_plain(self, text: str) -> list[str]:
        # 1. 有换行就按换行拆（模型自己分段的通常就是要分开发）
        if "\n" in text:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if len(lines) > 1:
                return lines

        # 2. 按句末标点拆（句号/问号/感叹号/省略号/波浪号）
        sents = re.split(r"(?<=[。！？!?…~])", text)
        sents = [s.strip() for s in sents if s.strip()]

        # 3. 没有句末标点但很长 → 按逗号拆
        if len(sents) <= 1 and len(text) > self.split_threshold:
            sents = [s.strip() for s in re.split(r"(?<=[，,])", text) if s.strip()]

        if len(sents) <= 1:
            return [text]

        # 4. 只合并"极短"的碎片（<8字），避免把句子又粘回一大段
        merged = []
        buf = ""
        for s in sents:
            if not buf:
                buf = s
            elif len(buf) < 8:          # 上一句太短，跟下一句拼
                buf += s
            else:
                merged.append(buf)
                buf = s
        if buf:
            merged.append(buf)
        return merged

    # ---------- 发送 ----------

    async def send_with_strategy(self, send_func, text: str):
        """按拟人化策略逐条发送"""
        if not text:
            return

        parts = self.split(text)
        logger.info(f"📤 分 {len(parts)} 条发送: {[p[:15] for p in parts]}")

        for i, part in enumerate(parts):
            if i == 0:
                await self.think_delay(part)
            else:
                # 条与条之间：像真人连续打字
                await asyncio.sleep(random.uniform(0.25, 0.7))

            try:
                await send_func(part)
            except Exception as e:
                logger.error(f"发送失败: {e}")
                break
