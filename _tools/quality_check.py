"""对话质量抽查：用新人设 + V4.1-Flash 生成几条回复，看长短和语气"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from app.config import config
from app.persona.loader import PersonaManager
from app.llm.client import LLMClient
from app.llm.prompt import PromptBuilder


CASES = [
    "在吗",
    "今天好累啊",
    "你觉得我胖吗",
    "帮我查下今天有什么科技新闻",
    "晚安",
    "你喜欢吃什么",
]


async def main():
    mgr = PersonaManager(config.PERSONA_DIR, config.USER_PERSONA_DIR)
    mgr.set_current(config.PERSONA)
    persona = mgr.current
    print(f"persona={persona.name} prompt_len={len(persona.system_prompt)}")

    llm = LLMClient(config.LLM_API_KEY, config.LLM_BASE_URL, config.LLM_MODEL,
                    timeout=config.LLM_TIMEOUT)

    for msg in CASES:
        builder = PromptBuilder(persona.system_prompt, 20)
        messages = builder.build(
            history=[], profile_text="", nickname="小主人",
            user_message=msg, msg_count=5,
        )
        reply = await llm.chat(messages, temperature=persona.temperature,
                               max_tokens=config.LLM_MAX_TOKENS)
        r = (reply or "<EMPTY>").replace("\n", " / ")
        print(f"\n>>> {msg}\n<<< [{len(reply or '')}字] {r}")


asyncio.run(main())
