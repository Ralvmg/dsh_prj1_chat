"""语音合成（TTS）—— 免费方案优先

优先级：
1. edge-tts（微软 Edge 免费语音，不限量、零成本）← 默认
2. 硅基流动 CosyVoice2（付费，作为兜底）

节流与缓存（省钱关键）：
- 同一段文字 → 内存缓存，重复的话不再合成
- 最短间隔：连续对话时不会每条都合成（默认 8 秒内只合成一次）
- 长度上限 200 字，超长直接跳过（语音听不完也没意义）
"""

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ============ edge-tts 中文女声（免费） ============
EDGE_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",       # 晓晓：甜美亲切（默认）
    "xiaoyi": "zh-CN-XiaoyiNeural",           # 晓伊：少女音
    "xiaobei": "zh-CN-liaoning-XiaobeiNeural",# 晓北：东北腔，活泼
    "xiaohan": "zh-CN-XiaohanNeural",         # 晓涵：温柔
    "xiaomeng": "zh-CN-XiaomengNeural",       # 晓梦：可爱
    "xiaomo": "zh-CN-XiaomoNeural",           # 晓墨：知性
}
EDGE_DEFAULT = "xiaoyi"      # 少女音最适合鲸鱼娘

# ============ 硅基流动女声（付费兜底） ============
SILICONFLOW_VOICES = {
    "anna": "anna",      # 年轻清澈
    "bella": "bella",    # 柔和
    "claire": "claire",  # 甜美
    "diana": "diana",    # 成熟
}


class TTSEngine:
    def __init__(self, api_key: str = "", base_url: str = "",
                 model: str = "FunAudioLLM/CosyVoice2-0.5B",
                 enabled: bool = True, timeout: int = 30,
                 provider: str = "edge",
                 min_interval: float = 8.0,
                 max_chars: int = 200):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.enabled = enabled
        self.timeout = httpx.Timeout(float(timeout), connect=8.0)
        self.provider = provider            # "edge" / "siliconflow" / "off"
        self.min_interval = float(min_interval)   # 两次合成最短间隔（秒）
        self.max_chars = int(max_chars)

        self._cache: dict[str, tuple[str, float]] = {}   # hash -> (b64, ts)
        self._cache_max = 200                            # 最多缓存 200 条
        self._last_call = 0.0

    # ---------- 节流 ----------

    def _should_skip(self, text: str) -> bool:
        """节流判断：太频繁 / 太长 / 重复 → 跳过"""
        if not self.enabled or self.provider == "off":
            return True
        if len(text) > self.max_chars:
            return True
        # 最短间隔：连续对话时不每条都合成
        if time.time() - self._last_call < self.min_interval:
            return True
        return False

    def _cache_key(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    def _get_cache(self, text: str) -> Optional[str]:
        key = self._cache_key(text)
        hit = self._cache.get(key)
        if hit:
            b64, ts = hit
            # 缓存 30 分钟
            if time.time() - ts < 1800:
                return b64
            del self._cache[key]
        return None

    def _put_cache(self, text: str, b64: str):
        if len(self._cache) >= self._cache_max:
            # 简单清理：删最旧的 50 条
            items = sorted(self._cache.items(), key=lambda kv: kv[1][1])
            for k, _ in items[:50]:
                self._cache.pop(k, None)
        self._cache[self._cache_key(text)] = (b64, time.time())

    # ---------- 主入口 ----------

    async def synthesize(self, text: str, voice: str = "",
                         speed: float = 1.0) -> Optional[str]:
        """合成语音，返回 base64 mp3；跳过/失败返回 None"""
        clean = self._clean(text)
        if not clean:
            return None

        # 缓存命中
        cached = self._get_cache(clean)
        if cached:
            return cached

        # 节流
        if self._should_skip(clean):
            return None

        self._last_call = time.time()

        audio = None
        if self.provider == "edge":
            audio = await self._edge_tts(clean, voice, speed)
            if audio is None and self.api_key:
                # edge 失败才走付费兜底
                logger.warning("edge-tts 失败，尝试硅基流动兜底")
                audio = await self._siliconflow(clean, voice, speed)
        elif self.provider == "siliconflow":
            audio = await self._siliconflow(clean, voice, speed)

        if audio:
            self._put_cache(clean, audio)
        return audio

    # ---------- 文本清洗 ----------

    def _clean(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        clean = re.sub(r"\[CQ:[^\]]+\]", "", text)
        clean = re.sub(r"\[[^\]]*\]", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return ""
        if len(clean) > self.max_chars:
            clean = clean[: self.max_chars]
        return clean

    # ---------- edge-tts（免费） ----------

    async def _edge_tts(self, text: str, voice: str, speed: float) -> Optional[str]:
        try:
            import edge_tts
        except ImportError:
            logger.warning("edge-tts 未安装")
            return None

        vname = EDGE_VOICES.get(voice or EDGE_DEFAULT, voice or EDGE_DEFAULT)
        if not vname.startswith("zh-"):
            vname = EDGE_VOICES[EDGE_DEFAULT]

        # 语速：edge 用百分比表示，1.0 → +0%
        rate_pct = int((float(speed) - 1.0) * 100)
        rate_str = f"{rate_pct:+d}%"

        try:
            communicate = edge_tts.Communicate(text, vname, rate=rate_str)
            buf = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if not buf:
                return None
            return base64.b64encode(bytes(buf)).decode()
        except Exception as e:
            logger.warning(f"edge-tts 异常: {e}")
            return None

    # ---------- 硅基流动（付费兜底） ----------

    async def _siliconflow(self, text: str, voice: str, speed: float) -> Optional[str]:
        if not self.api_key:
            return None
        vname = voice or "anna"
        if ":" not in vname:
            vname = f"{self.model}:{vname}"
        payload = {
            "model": self.model,
            "input": text,
            "voice": vname,
            "response_format": "mp3",
            "speed": max(0.5, min(2.0, float(speed))),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(
                    f"{self.base_url}/audio/speech",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if r.status_code != 200:
                    logger.error(f"硅基流动 TTS {r.status_code}: {r.text[:150]}")
                    return None
                return base64.b64encode(r.content).decode() if r.content else None
        except Exception as e:
            logger.error(f"硅基流动 TTS 异常: {e}")
            return None

    def list_voices(self) -> dict:
        if self.provider == "edge":
            return dict(EDGE_VOICES)
        return dict(SILICONFLOW_VOICES)
