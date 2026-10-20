"""提示词构建 —— 只提供"事实"，不教"怎么说"

设计原则（参照 AstrBot）：
- 人设只写"你是谁"，不写"你该怎么说话"
- 记忆/上下文以事实形式给出，不加"要用什么语气""别复述"这类指令
- 把表达方式完全交给模型自己判断
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class PromptBuilder:
    def __init__(self, persona_prompt: str, history_limit: int = 40):
        self.persona_prompt = persona_prompt
        self.history_limit = history_limit

    def build(self, *, history: list[dict], profile_text: str,
              nickname: str, user_message: str,
              msg_count: int = 0, group_persona: str = "",
              is_group: bool = False, mood_hint: str = "",
              older_summary: str = "", facts: list[str] = None,
              terms: dict = None, image_refs: list[str] = None) -> list[dict]:
        """构建完整的消息列表"""

        system_parts = [self.persona_prompt]

        # 时间
        now = datetime.now()
        system_parts.append(f"\n现在是 {now.strftime('%Y年%m月%d日 %H:%M')} 星期{'日一二三四五六'[now.weekday()+1 if now.weekday()<6 else 0]}")

        # 心情（作为事实陈述，不是行为指令）
        if mood_hint:
            system_parts.append(f"\n你现在的状态：{mood_hint}")

        # 更早的对话摘要
        if older_summary:
            system_parts.append(f"\n更早聊过的：{older_summary}")

        # 关于这个人记得的事
        if facts:
            system_parts.append("\n关于「%s」你记得：\n%s"
                                % (nickname, "\n".join(f"- {f}" for f in facts)))

        # 群内说法
        if terms:
            terms_text = "、".join(f"{k}({v})" for k, v in list(terms.items())[:20])
            system_parts.append(f"\n这个群里的一些说法：{terms_text}")

        # 群设定
        if is_group and group_persona:
            system_parts.append(f"\n本群：{group_persona}")

        # 用户画像
        if profile_text:
            system_parts.append(f"\n「{nickname}」的印象：{profile_text}")

        # 图片提示：只说"对方发了图"，不说该怎么反应
        if image_refs:
            system_parts.append("\n（对方发了图片，你能看到）")

        # 历史条数
        if self.history_limit and len(history) > self.history_limit:
            history = history[-self.history_limit:]

        if msg_count > 3:
            system_parts.append(f"\n（和这个人聊过 {msg_count} 次）")

        messages = [{"role": "system", "content": "\n".join(system_parts)}]

        # 历史对话
        for h in history:
            if h["role"] == "user":
                prefix = f"{h['nickname']}: " if h.get("nickname") else ""
                messages.append({"role": "user", "content": prefix + h["content"]})
            elif h["role"] == "assistant":
                messages.append({"role": "assistant", "content": h["content"]})

        # 当前消息（多模态：图片直接放进 content）
        user_text = f"{nickname}: {user_message}"
        if image_refs:
            content_parts = [{"type": "text", "text": user_text}]
            for ref in image_refs[:3]:
                content_parts.append({"type": "image_url", "image_url": {"url": ref}})
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_text})

        return messages
