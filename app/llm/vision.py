"""视觉模型客户端 —— 让机器人"看见"图片、表情包、动图首帧

硅基流动有 Qwen2.5-VL 系列，OpenAI 兼容格式，image_url 支持 base64 data URL。
"""

import base64
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# NapCat 容器路径 → app 容器可见路径
# napcat 的 data 挂在宿主机 ./data/napcat，app 容器把 ./data 挂到 /app/data
PATH_MAP = [
    ("/app/napcat/data/", "/app/data/napcat/"),
    ("/app/napcat/cache/", "/app/data/napcat/cache/"),
    ("/app/.config/QQ/", "/app/qqcfg/"),          # 若把 QQ 数据目录也挂进来
    ("/app/qqcfg/", "/app/qqcfg/"),
]

MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB，超过就跳过


class VisionClient:
    def __init__(self, api_key: str, base_url: str, model: str,
                 enabled: bool = True, timeout: int = 40):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = enabled
        self.timeout = httpx.Timeout(float(timeout), connect=10.0)

    # ---------- 取图片 ----------

    def _resolve_local(self, file_path: str) -> Optional[Path]:
        """把 napcat 容器内路径映射成本容器可读路径"""
        if not file_path:
            return None
        p = Path(file_path)
        for src, dst in PATH_MAP:
            if file_path.startswith(src):
                p = Path(dst + file_path[len(src):])
                break
        return p if p.exists() else None

    async def load_image_ref(self, url: str, file_path: str = "") -> Optional[str]:
        """把图片变成可直接塞进多模态消息的引用（data URL 或 http URL）"""
        return await self._load_image(url, file_path)

    async def load_images(self, images: list[dict], max_count: int = 2) -> list[str]:
        """批量加载图片引用"""
        refs = []
        for img in images[:max_count]:
            ref = await self._load_image(img.get("url", ""), img.get("file", ""))
            if ref:
                refs.append(ref)
        return refs

    async def _load_image(self, url: str, file_path: str) -> Optional[str]:
        """返回 data URL 或原始 http URL

        优先用 URL（跨容器也能访问）；本地文件只作兜底，
        因为 NapCat 的 QQ 数据在它自己的卷里，app 容器通常读不到。
        """
        # 1. 优先 URL —— 模型服务端能直接拉取
        if url and url.startswith("http"):
            return url

        # 2. 本地文件兜底
        local = self._resolve_local(file_path)
        if local:
            try:
                size = local.stat().st_size
                if size > MAX_IMAGE_BYTES:
                    logger.info(f"图片过大跳过: {size} 字节")
                    return None
                data = local.read_bytes()
                mime = "image/png" if data[:4] == b"\x89PNG" else "image/gif" if data[:3] == b"GIF" else "image/jpeg"
                return f"data:{mime};base64,{base64.b64encode(data).decode()}"
            except Exception as e:
                logger.warning(f"读取本地图片失败 {local}: {e}")

        return None

    # ---------- 描述图片 ----------

    async def describe(self, url: str, file_path: str = "", hint: str = "") -> Optional[str]:
        """描述一张图片；失败返回 None"""
        if not self.enabled or not self.api_key:
            return None

        image_ref = await self._load_image(url, file_path)
        if not image_ref:
            return None

        prompt = (
            "简短描述这张图片的内容（1-2 句）："
            "如果是表情包/梗图，说明画面是什么、想表达什么情绪；"
            "如果是照片/截图，说明里面有什么、大致场景。"
            "只输出描述，不要客套话。"
        )
        if hint:
            prompt += f"（背景：{hint}）"

        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_ref}},
                ],
            }],
            "temperature": 0.3,
            "max_tokens": 150,
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
                    logger.error(f"视觉模型 {resp.status_code}: {resp.text[:200]}")
                    return None
                data = resp.json()
                content = data["choices"][0]["message"].get("content")
                return content.strip() if content else None
        except httpx.TimeoutException:
            logger.warning("视觉模型超时")
            return None
        except Exception as e:
            logger.error(f"视觉模型异常: {e}")
            return None

    async def describe_images(self, images: list[dict], max_count: int = 2) -> list[str]:
        """批量描述（最多 N 张，避免慢）"""
        results = []
        for img in images[:max_count]:
            desc = await self.describe(
                url=img.get("url", ""),
                file_path=img.get("file", ""),
            )
            if desc:
                results.append(desc)
        return results
