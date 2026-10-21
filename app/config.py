"""配置集中管理 —— 所有配置从环境变量读取，带默认值和类型校验"""

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


class Config:
    # ---------- 路径 ----------
    DATA_DIR = Path(_env("DATA_DIR", "/app/data"))
    LOG_DIR = Path(_env("LOG_DIR", "/app/logs"))
    DB_PATH = _env("DB_PATH", "/app/data/qqchat.db")

    # ---------- OneBot 连接 ----------
    ONEBOT_WS_URL = _env("ONEBOT_WS_URL", "ws://napcat:3001")
    ONEBOT_ACCESS_TOKEN = _env("ONEBOT_ACCESS_TOKEN", "")
    RECONNECT_DELAY = _env_int("RECONNECT_DELAY", 5)

    # ---------- LLM ----------
    LLM_API_KEY = _env("LLM_API_KEY", "")
    LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
    LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.9)
    LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 60)

    # ---------- 人设 ----------
    PERSONA = _env("PERSONA", "dafeiyu")
    PERSONA_DIR = _env("PERSONA_DIR", "/app/persona/builtin")
    USER_PERSONA_DIR = _env("USER_PERSONA_DIR", "/app/data/personas")
    BOT_NAME = _env("BOT_NAME", "")

    # ---------- 回复策略 ----------
    HISTORY_LIMIT = _env_int("HISTORY_LIMIT", 40)          # 上下文携带的历史条数
    REPLY_DELAY_MIN = _env_float("REPLY_DELAY_MIN", 0.2)   # 回复前最小延迟（秒）
    REPLY_DELAY_MAX = _env_float("REPLY_DELAY_MAX", 0.9)   # 最大延迟
    SPLIT_REPLY = _env_bool("SPLIT_REPLY", True)           # 长回复是否拆成多条
    SPLIT_THRESHOLD = _env_int("SPLIT_THRESHOLD", 40)      # 超过多少字符拆分（越小越像真人连发）
    MOOD_PERSONA_SWITCH = _env_bool("MOOD_PERSONA_SWITCH", True)  # 心情自动切人格

    # ---------- 群聊策略 ----------
    GROUP_TRIGGERS = [t.strip() for t in _env("GROUP_TRIGGERS", "").split(",") if t.strip()]
    GROUP_REPLY_PROB = _env_float("GROUP_REPLY_PROB", 1.0)  # 命中触发词后的回复概率
    GROUP_AT_ONLY = _env_bool("GROUP_AT_ONLY", False)       # 群聊是否只响应 @

    # ---------- LLM 输出 ----------
    # 不限制回复长度：让模型自己决定说几个字（"哦" 或 一长段都行）
    LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 4096)

    # ---------- 限流与 token 保护（按真人节奏放宽）----------
    RATE_PER_MIN = _env_int("RATE_PER_MIN", 15)           # 单会话每分钟最多回复
    RATE_PER_HOUR = _env_int("RATE_PER_HOUR", 120)        # 单会话每小时最多回复
    GLOBAL_PER_MIN = _env_int("GLOBAL_PER_MIN", 40)       # 全局每分钟最多回复
    MERGE_WINDOW_SEC = _env_float("MERGE_WINDOW_SEC", 1.2)      # 首次等待（短，保证响应快）
    INTERRUPT_WAIT_SEC = _env_float("INTERRUPT_WAIT_SEC", 3.0)  # 回复准备中又收到消息，停下等多久
    MAX_MERGE_WAIT_SEC = _env_float("MAX_MERGE_WAIT_SEC", 10.0) # 合并最长等待，防止一直等
    DEDUPE_WINDOW_SEC = _env_float("DEDUPE_WINDOW_SEC", 1.0)    # 1 秒内完全相同的消息视为重复
    DAILY_CHAR_BUDGET = _env_int("DAILY_CHAR_BUDGET", 200000)  # 每日字符预算

    PROFILE_UPDATE_INTERVAL = _env_int("PROFILE_UPDATE_INTERVAL", 10)  # 每 N 条更新画像
    PROFILE_UPDATE_COOLDOWN = _env_int("PROFILE_UPDATE_COOLDOWN", 600) # 冷却秒数
    HISTORY_KEEP_DAYS = _env_int("HISTORY_KEEP_DAYS", 30)
    HISTORY_KEEP_PER_SESSION = _env_int("HISTORY_KEEP_PER_SESSION", 200)
    # 超出窗口多少条后触发压缩（HISTORY_LIMIT + 此值 = 触发阈值）
    COMPRESS_TRIGGER_EXTRA = _env_int("COMPRESS_TRIGGER_EXTRA", 10)
    # 定时清理间隔（小时）
    CLEANUP_INTERVAL_HOURS = _env_int("CLEANUP_INTERVAL_HOURS", 24)

    # ---------- 工具 ----------
    TOOL_MAX_ROUNDS = _env_int("TOOL_MAX_ROUNDS", 8)  # 工具调用最大轮次

    # ---------- 服务器操作（严格入口 + 会话模式）----------
    # 只有这些 QQ 号、在私聊里、且发出口令时才进入操作模式
    # 管理员的 QQ 号通过环境变量配置，不要写死在代码里
    ADMIN_QQ = [q.strip() for q in _env("ADMIN_QQ", "").split(",") if q.strip()]
    ADMIN_PREFIX = _env("ADMIN_PREFIX", "!!")
    OPS_ENTRY = _env("OPS_ENTRY", "/help me for linux by ra 666")
    OPS_EXIT = _env("OPS_EXIT", "/exit help")
    OPS_IDLE_SEC = _env_int("OPS_IDLE_SEC", 60)
    AGENT_URL = _env("AGENT_URL", "")
    AGENT_TOKEN = _env("AGENT_TOKEN", "")
    AGENT_TIMEOUT = _env_int("AGENT_TIMEOUT", 90)

    # ---------- 视觉（看图 / 表情包）----------
    VISION_ENABLED = _env_bool("VISION_ENABLED", True)
    # 主模型本身多模态时，图片直接进上下文；这个模型只在兜底描述时用
    VISION_MODEL = _env("VISION_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
    VISION_MAX_IMAGES = _env_int("VISION_MAX_IMAGES", 2)
    VISION_FALLBACK_DESCRIBE = _env_bool("VISION_FALLBACK_DESCRIBE", False)

    # ---------- 联网（搜索/抓网页/热点）----------
    WEB_ENABLED = _env_bool("WEB_ENABLED", True)
    WEB_CACHE_TTL = _env_int("WEB_CACHE_TTL", 1800)   # 缓存秒数

    # ---------- 学习 ----------
    LEARNING_ENABLED = _env_bool("LEARNING_ENABLED", True)
    LEARNING_INTERVAL_MIN = _env_int("LEARNING_INTERVAL_MIN", 30)

    # ---------- Web 聊天 ----------
    WEB_CHAT_ENABLED = _env_bool("WEB_CHAT_ENABLED", True)
    WEB_CHAT_HOST = _env("WEB_CHAT_HOST", "0.0.0.0")
    WEB_CHAT_PORT = _env_int("WEB_CHAT_PORT", 8080)

    # ---------- 语音合成 ----------
    # provider: siliconflow（CosyVoice2，音质好）/ edge（免费但机械）/ off
    TTS_ENABLED = _env_bool("TTS_ENABLED", True)
    TTS_PROVIDER = _env("TTS_PROVIDER", "siliconflow")
    TTS_MODEL = _env("TTS_MODEL", "FunAudioLLM/CosyVoice2-0.5B")
    TTS_VOICE = _env("TTS_VOICE", "claire")       # claire: 甜美少女音
    TTS_MIN_INTERVAL = _env_float("TTS_MIN_INTERVAL", 8.0)   # 两次合成最短间隔（秒）
    TTS_MAX_CHARS = _env_int("TTS_MAX_CHARS", 200)           # 超过多少字不合成

    # ---------- 日志 ----------
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")

    @classmethod
    def ensure_dirs(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (cls.DATA_DIR / "personas").mkdir(parents=True, exist_ok=True)


config = Config()

