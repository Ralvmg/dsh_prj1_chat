"""LLM 客户端 —— 兼容 OpenAI 格式的 API 调用"""

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: int = 60):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = httpx.Timeout(float(timeout), connect=10.0)
        if not api_key:
            logger.error("⚠️  LLM_API_KEY 未配置，机器人无法回复！")

    async def chat_raw(self, messages: list[dict], tools: Optional[list] = None,
                       temperature: float = 0.9, max_tokens: int = 4096) -> Optional[dict]:
        """调用 LLM，返回原始结构（支持工具调用）

        返回:
            {"type": "text", "content": "..."}
            {"type": "tool_calls", "tool_calls": [...], "content": "..."}
        """
        if not self.api_key:
            return None

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    logger.error(f"LLM API {resp.status_code}: {resp.text[:300]}")
                    return None

                data = resp.json()
                msg = data["choices"][0]["message"]

                if msg.get("tool_calls"):
                    calls = []
                    for tc in msg["tool_calls"]:
                        try:
                            args = json.loads(tc["function"].get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        calls.append({
                            "id": tc.get("id", ""),
                            "name": tc["function"].get("name", ""),
                            "arguments": args,
                        })
                    return {"type": "tool_calls", "tool_calls": calls,
                            "content": msg.get("content")}

                content = msg.get("content")
                return {"type": "text", "content": content.strip() if content else None}

        except httpx.TimeoutException:
            logger.error("LLM API 超时")
            return None
        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            return None

    async def chat(self, messages: list[dict], temperature: float = 0.9,
                   max_tokens: int = 4096) -> Optional[str]:
        """调用 LLM，返回文本。失败返回 None。"""
        if not self.api_key:
            return None

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    logger.error(f"LLM API {resp.status_code}: {resp.text[:300]}")
                    return None

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content.strip() if content else None

        except httpx.TimeoutException:
            logger.error("LLM API 超时")
            return None
        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            return None

    async def summarize_profile(self, conversation_text: str,
                                old_profile: str = "") -> Optional[str]:
        """从对话提炼用户画像"""
        prompt = f"""阅读以下对话，提炼这个人的画像。只写关键信息，简洁。

已有画像（可补充修正）：
{old_profile or '（无）'}

对话记录：
{conversation_text}

输出要求：
- 直接写结论，不要写"用户说"之类的转述
- 格式为简短要点：身份/职业、兴趣、性格、聊天风格、最近在关注什么
- 控制在 150 字以内
- 不确定的不要瞎猜"""

        return await self.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=800,
        )

    async def summarize_history(self, old_conversation: str,
                                previous_summary: str = "") -> Optional[str]:
        """把一段旧对话压缩成记忆摘要（长对话不失忆）"""
        prompt = f"""把下面这段聊天记录压缩成一段"记忆摘要"。

已有的更早记忆：
{previous_summary or '（无）'}

需要压缩的聊天记录：
{old_conversation[:3000]}

输出要求：
- 用第三人称、简练地记录：聊了什么话题、对方透露的信息、约定的事、情绪变化
- 合并到已有记忆里（不要丢弃旧记忆里重要的内容）
- 保留具体细节（人名、偏好、事件），不要泛泛而谈
- 控制在 300 字以内
- 只输出摘要正文"""

        return await self.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=1500,
        )


