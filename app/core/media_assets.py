"""表情/动画资源管理 —— 鲸鱼娘的 20 个动画 + 24 张表情包

素材来源：dsh-pet-indesktop（MerZlin/dsh-pet-indesktop）
动画由 WebM 转成透明 GIF，供 QQ 发送。

用法：
- 按情绪选动画（happy/angry/shy/laugh/...）
- 按场景选表情包（吃米饭/写代码/没钱/...）
- 返回本地文件路径，发送时转成 CQ:image base64
"""

import base64
import logging
import os
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# 动画：情绪 → 文件名
ANIMATIONS = {
    # 基础情绪
    "happy": "happy.gif",          # 开心跃动
    "angry": "angry.gif",          # 傲娇生气
    "shy": "shy.gif",              # 害羞惊讶
    "laugh": "laugh.gif",          # 挠痒咯咯笑
    "wave": "wave.gif",            # 元气挥手
    "scared": "scared.gif",        # 被吓一跳
    "frustrated": "frustrated.gif",# 玩游戏气急败坏
    # 状态
    "sleep": "sleep.gif",          # 原地小憩沉眠
    "doze": "doze.gif",            # 打瞌睡被惊醒
    "yawn": "yawn.gif",            # 哈欠连天
    "stretch": "stretch.gif",      # 超大伸懒腰
    "idle": "idle.gif",            # 待机呼吸
    "thinking": "thinking.gif",    # 深度思考碎碎念
    "coding": "coding.gif",        # 写代码
    "humming": "humming.gif",      # 悠闲哼歌
    # 特色
    "eat_rice": "eat_rice.gif",    # 吃白饭（招牌）
    "eat_token": "eat_token.gif",  # 吃Token
    "bubble": "bubble.gif",        # 鲸鱼吐泡泡
    "tail_slap": "tail_slap.gif",  # 尾巴拍地
    "caught": "caught.gif",        # 偷吃被抓住
}

# 表情包：场景 → 文件名
STICKERS = {
    "main": "main.jpg",            # 招牌形象
    "fat": "fat.jpg",              # 胖鱼
    "cute": "cute.png",            # 可爱
    "hajimi": "hajimi.png",        # 哈基米
    "dead": "dead.jpg",            # 死鱼
    "deepsleep": "deepsleep.jpg",  # 沉睡
    "curse": "curse.jpg",          # 咒骂
    "agi": "agi.png",              # 去AGI
    "nomoney": "nomoney.jpg",      # 没钱
    "push": "push.jpg",            # 推鱼
    "quiet": "quiet.jpg",          # 安静
    "mama": "mama.png",            # 妈妈
    "eval": "eval.png",            # 可评价
    "uneval": "uneval.png",        # 不可评价
    "update": "update.jpg",        # 更新
    "virus": "virus.jpg",          # 病毒
    "yupian": "yupian.png",        # 鱼片
    "kaidian": "kaidian.jpg",      # 开店
    "multimodal": "multimodal.jpg",# 多模态
    "boss": "boss.png",            # 大佬来了
    "bigcoming": "bigcoming.jpg",  # 大事件
    "dsh": "dsh.jpg",              # DSH
}

# 情绪关键词 → 动画（供模型/自动选择）
EMOTION_HINTS = {
    "开心": ["happy", "laugh", "wave"],
    "高兴": ["happy", "laugh"],
    "笑": ["laugh", "happy"],
    "生气": ["angry", "tail_slap"],
    "恼": ["angry", "frustrated"],
    "害羞": ["shy"],
    "尴尬": ["shy", "scared"],
    "困": ["sleep", "yawn", "doze"],
    "累": ["sleep", "stretch", "yawn"],
    "思考": ["thinking"],
    "想": ["thinking"],
    "吃": ["eat_rice", "eat_token"],
    "米饭": ["eat_rice"],
    "无聊": ["idle", "humming"],
    "开心": ["happy"],
}


class MediaAssets:
    def __init__(self, base_dir: str = "/app/assets"):
        self.base = Path(base_dir)
        self.anim_dir = self.base / "animations"
        self.sticker_dir = self.base / "stickers"

    def _find(self, folder: Path, name: str) -> Path | None:
        p = folder / name
        return p if p.exists() else None

    def get_animation(self, key: str) -> Path | None:
        fname = ANIMATIONS.get(key)
        return self._find(self.anim_dir, fname) if fname else None

    def get_sticker(self, key: str) -> Path | None:
        fname = STICKERS.get(key)
        return self._find(self.sticker_dir, fname) if fname else None

    def pick_by_emotion(self, text: str) -> Path | None:
        """从文本里识别情绪，挑一个合适的动画"""
        for kw, keys in EMOTION_HINTS.items():
            if kw in text:
                key = random.choice(keys)
                p = self.get_animation(key)
                if p:
                    return p
        return None

    def pick_sticker_by_context(self, text: str) -> Path | None:
        """从上下文挑表情包"""
        rules = [
            (["米饭", "吃饭", "吃"], "eat_rice" if False else "main"),
            (["胖", "肥"], "fat"),
            (["可爱", "萌"], "cute"),
            (["钱", "穷", "额度", "token"], "nomoney"),
            (["睡", "困"], "deepsleep"),
            (["代码", "bug", "编程"], "dsh"),
            (["AGI", "agi"], "agi"),
            (["更新", "升级"], "update"),
            (["病毒", "报错", "错误"], "virus"),
        ]
        for kws, key in rules:
            if any(k in text for k in kws):
                p = self.get_sticker(key)
                if p:
                    return p
        return None

    @staticmethod
    def to_data_url(path: Path, max_bytes: int = 2 * 1024 * 1024) -> str | None:
        """把本地图片转成 data URL（QQ CQ 码可用）"""
        try:
            if not path or not path.exists():
                return None
            size = path.stat().st_size
            if size > max_bytes:
                logger.warning(f"图片过大: {path.name} {size} 字节")
                return None
            data = path.read_bytes()
            if data[:4] == b"\x89PNG":
                mime = "image/png"
            elif data[:3] == b"GIF":
                mime = "image/gif"
            elif data[:2] == b"\xff\xd8":
                mime = "image/jpeg"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                mime = "image/png"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
        except Exception as e:
            logger.warning(f"读取图片失败 {path}: {e}")
            return None

    def list_animations(self) -> list[str]:
        return sorted(ANIMATIONS.keys())

    def list_stickers(self) -> list[str]:
        return sorted(STICKERS.keys())

    def stats(self) -> dict:
        a = len(list(self.anim_dir.glob("*.gif"))) if self.anim_dir.exists() else 0
        s = len(list(self.sticker_dir.glob("*"))) if self.sticker_dir.exists() else 0
        return {"animations": a, "stickers": s}
