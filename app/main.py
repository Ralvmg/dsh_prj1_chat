"""QQChat 机器人 —— 程序入口

职责：初始化各层、连接 OneBot、启动事件循环。
业务逻辑不在这里，见 core/handler.py。
"""

import asyncio
import logging
import signal
import sys
import time

from .config import config
from .memory import Memory
from .memory.mood import MoodEngine
from .llm import LLMClient
from .persona.loader import PersonaManager
from .core.onebot import OneBotClient
from .core.handler import MessageHandler
from .core.reply import ReplyStrategy
from .core.proactive import ProactiveEngine
from .core.learning import LearningEngine
from .core.ratelimit import RateLimiter
from .core.serverops import ServerOps
from .core.opssession import OpsSessionManager
from .core.media_assets import MediaAssets
from .core.webserver import WebChatServer
from .llm.tts import TTSEngine
from .core.segments import describe_event
from .llm.tools import ToolExecutor, TOOLS
from .llm.vision import VisionClient
from .llm.web import WebClient


def setup_logging():
    config.ensure_dirs()
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_DIR / "qqchat.log", encoding="utf-8"),
        ],
    )


async def main():
    setup_logging()
    logger = logging.getLogger("qqchat")

    logger.info("=" * 50)
    logger.info("QQChat 启动中...")
    logger.info("=" * 50)

    # 1. 记忆
    memory = Memory(config.DB_PATH)
    await memory.connect()

    # 2. 人设
    persona_manager = PersonaManager(config.PERSONA_DIR, config.USER_PERSONA_DIR)
    if not persona_manager.set_current(config.PERSONA):
        available = list(persona_manager.list_all().keys())
        logger.error(f"人设 '{config.PERSONA}' 加载失败，可用: {available}")
        raise SystemExit(1)
    logger.info(f"当前人设: {persona_manager.current.name}")

    # 3. LLM
    llm = LLMClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
        timeout=config.LLM_TIMEOUT,
    )
    logger.info(f"LLM: {config.LLM_MODEL} @ {config.LLM_BASE_URL}")

    # 4. 消息处理
    handler = MessageHandler(memory, llm, persona_manager)

    # 5. 心情引擎
    mood = MoodEngine(memory.db)
    await mood.load()
    logger.info(f"心情状态: {mood.current} (能量 {mood.energy:.0f})")
    handler.mood = mood

    # 6. 主动行为引擎
    proactive = ProactiveEngine(memory, llm, persona_manager, mood)

    # 7. OneBot 客户端
    client = OneBotClient(
        ws_url=config.ONEBOT_WS_URL,
        access_token=config.ONEBOT_ACCESS_TOKEN,
        reconnect_delay=config.RECONNECT_DELAY,
    )
    proactive.client = client

    # 7.5 工具系统（让机器人能查群信息、改状态等）
    tool_executor = ToolExecutor(client, memory, mood)
    handler.tool_executor = tool_executor

    # 7.6 视觉能力（看懂图片/表情包）
    vision = VisionClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.VISION_MODEL,
        enabled=config.VISION_ENABLED,
    )
    handler.vision = vision

    # 7.7 联网能力（搜索/抓网页/热点）
    web = WebClient(db=memory.db, cache_ttl=config.WEB_CACHE_TTL)
    logger.info(f"联网能力: {'已启用' if config.WEB_ENABLED else '已关闭'}")

    # 7.8 学习引擎（学用户事实、群内黑话）
    learning = LearningEngine(memory, llm, interval_min=config.LEARNING_INTERVAL_MIN)
    logger.info(f"学习引擎: 每 {config.LEARNING_INTERVAL_MIN} 分钟")

    # 把依赖注入工具执行器（自检/学习/联网工具要用）
    tool_executor.llm = llm
    tool_executor.vision = vision
    tool_executor.web = web if config.WEB_ENABLED else None
    tool_executor.learning = learning if config.LEARNING_ENABLED else None
    tool_executor.persona_manager = persona_manager

    logger.info(f"工具系统已就绪（{len(TOOLS)} 个工具）")
    logger.info(f"视觉能力: {'已启用 ' + config.VISION_MODEL if config.VISION_ENABLED else '已关闭'}")

    # 7.9 限流 + 突发合并（token 保护）
    rate_limiter = RateLimiter(
        per_min=config.RATE_PER_MIN,
        per_hour=config.RATE_PER_HOUR,
        global_per_min=config.GLOBAL_PER_MIN,
        daily_char_budget=config.DAILY_CHAR_BUDGET,
    )
    handler.rate_limiter = rate_limiter
    tool_executor.rate_limiter = rate_limiter
    logger.info(f"限流保护: {config.RATE_PER_MIN}/分钟·会话, 日预算 {config.DAILY_CHAR_BUDGET} 字符")

    # 7.10 服务器操作（会话模式：口令进入，60 秒无操作自动退出）
    serverops = ServerOps(config.AGENT_URL, config.AGENT_TOKEN, config.AGENT_TIMEOUT)
    handler.serverops = serverops
    try:
        agent_ok = await serverops.health()
        logger.info("服务器操作代理: " + ("可用 " + config.AGENT_URL if agent_ok
                                           else "不可用（检查 qqagent 服务）"))
    except Exception as e:
        logger.warning(f"服务器操作代理检测失败: {e}")

    async def send_to_user(uid: str, msg: str):
        await client.send_private(uid, msg)

    ops_manager = OpsSessionManager(
        serverops, send_to_user, config.ADMIN_QQ,
        entry_phrase=config.OPS_ENTRY,
        exit_phrase=config.OPS_EXIT,
        idle_seconds=config.OPS_IDLE_SEC,
    )
    handler.ops_session = ops_manager
    tool_executor.ops_session = ops_manager
    logger.info(f"服务器操作入口: {config.OPS_ENTRY}（仅管理员 {config.ADMIN_QQ} 私聊；"
                f"{config.OPS_IDLE_SEC} 秒无操作退出）")

    # 7.11 鲸鱼娘表情/动画素材
    media = MediaAssets("/app/assets")
    tool_executor.media = media
    handler.media = media
    mst = media.stats()
    logger.info(f"素材库: {mst['animations']} 个动画, {mst['stickers']} 张表情包")

    # 7.12 Web 聊天服务（HTTP API，供网页前端调用）
    web_chat = None
    if config.WEB_CHAT_ENABLED:
        tts = TTSEngine(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            model=config.TTS_MODEL,
            enabled=config.TTS_ENABLED,
            provider=config.TTS_PROVIDER,
            min_interval=config.TTS_MIN_INTERVAL,
            max_chars=config.TTS_MAX_CHARS,
        )
        web_chat = WebChatServer(
            memory, llm, persona_manager, mood, tool_executor,
            host=config.WEB_CHAT_HOST, port=config.WEB_CHAT_PORT,
            tts=tts,
        )
        # 在后台线程跑 HTTP 服务，但把主事件循环传进去（跨 loop 会死锁）
        import threading
        loop = asyncio.get_running_loop()
        t = threading.Thread(target=web_chat.serve, args=(loop,),
                             daemon=True, name="webchat-http")
        t.start()
        logger.info(f"Web 聊天: http://{config.WEB_CHAT_HOST}:{config.WEB_CHAT_PORT}"
                    f"（语音: {config.TTS_PROVIDER if config.TTS_ENABLED else '关闭'}，"
                    f"间隔≥{config.TTS_MIN_INTERVAL}s）")

    # 每个会话的调度状态：攒消息 → 到点回复；准备中又收到消息 → 停下重等
    sched: dict[str, dict] = {}
    recent_texts: dict[str, tuple[str, float]] = {}   # 去重：session -> (text, ts)

    async def on_message(event: dict):
        """收到消息 → 处理 → 拟人化发送"""
        user_id = str(event.get("user_id", ""))
        text = handler.get_text(event)

        # 免打扰检测
        if text and proactive.handle_opt_out(text, user_id):
            await client.reply(event, "好，那我不找你了。想聊了再叫我。")
            return

        # 群消息进观察 buffer（供插嘴判断）
        if event.get("message_type") == "group":
            event["raw_message"] = event.get("raw_message") or handler.get_text(event)
            await proactive.observe_group_message(event)

        session_key = handler.session_key(event)
        is_command = text.strip().startswith("/")
        has_media = bool(describe_event(event)[1].get("has_media"))

        # 1 秒内完全相同的消息视为重复（防抖，不误伤正常连发）
        if not is_command:
            now = time.time()
            prev = recent_texts.get(session_key)
            if prev and prev[0] == text and now - prev[1] < config.DEDUPE_WINDOW_SEC:
                logger.info(f"重复消息忽略: {session_key}")
                return
            recent_texts[session_key] = (text, now)

        # 限流：命令永远放行
        if not is_command:
            ok, reason = rate_limiter.allow(session_key)
            if not ok:
                logger.info(f"限流跳过（{reason}）: {session_key}")
                if reason == "daily_budget":
                    await client.reply(event, "（今天话说太多了，本鲸鱼娘先省着点力气…）")
                return

        # 命令：立刻执行，不参与合并
        if is_command:
            await process(event, text, keep_media=has_media)
            return

        await schedule(session_key, text, event, has_media)

    async def schedule(session_key: str, text: str, event: dict, has_media: bool):
        """把消息排进调度队列

        - 空闲时：只等 MERGE_WINDOW_SEC（短，保证响应快）
        - 正在准备回复时又收到消息：取消当前准备，改等 INTERRUPT_WAIT_SEC（默认 3 秒）
          模拟"对方还在打字，先等等"的行为，也能省一次 LLM 调用
        """
        st = sched.setdefault(session_key, {
            "texts": [], "event": None, "timer": None,
            "proc": None, "first_ts": 0.0, "inflight": False,
        })

        # 正在准备回复 → 打断，攒起来重等
        if st["inflight"] and st["proc"] and not st["proc"].done():
            logger.info(f"⏸️ 准备回复时收到新消息，取消当前生成: {session_key}")
            st["proc"].cancel()

        st["texts"].append(text)
        st["event"] = event
        st["has_media"] = has_media
        if not st["first_ts"]:
            st["first_ts"] = time.time()

        # 已有待触发的定时器 → 重置（对方还在说）
        if st["timer"] and not st["timer"].done():
            st["timer"].cancel()

        delay = config.INTERRUPT_WAIT_SEC if st["inflight"] else config.MERGE_WINDOW_SEC
        st["timer"] = asyncio.create_task(_flush_after(session_key, delay))

    async def _flush_after(session_key: str, delay: float):
        await asyncio.sleep(delay)
        st = sched.get(session_key)
        if not st:
            return
        # 合并等待上限，避免一直等下去
        if st["first_ts"] and time.time() - st["first_ts"] > config.MAX_MERGE_WAIT_SEC:
            logger.info(f"⏰ 合并等待达到上限，立即回复: {session_key}")
        texts = st["texts"]
        event = st["event"]
        has_media = st.get("has_media", False)
        st["texts"] = []
        st["first_ts"] = 0.0
        if not texts or event is None:
            return

        merged_text = "\n".join(t for t in texts if t)
        st["inflight"] = True
        st["proc"] = asyncio.current_task()
        try:
            await process(event, merged_text, keep_media=has_media)
        except asyncio.CancelledError:
            # 被新消息打断：把文本还回去，等下一次合并
            st["texts"] = [merged_text] + st["texts"]
            st["inflight"] = False
            logger.info(f"⏹️ 回复被新消息打断，重新合并: {session_key}")
            raise
        finally:
            st["inflight"] = False
            st["proc"] = None

    async def process(event: dict, text: str, keep_media: bool = False):
        """真正进入处理流程

        keep_media=True 时保留原始消息段（图片/表情要靠它送进多模态模型）
        """
        merged_event = dict(event)
        if not keep_media:
            merged_event["message"] = [{"type": "text", "data": {"text": text}}]
        reply = await handler.handle(merged_event)
        if reply:
            async def send(t: str):
                await client.reply(event, t)
            await handler.reply_strategy.send_with_strategy(send, reply)

    client.on_message(on_message)

    # 8. 启动
    logger.info(f"连接 OneBot: {config.ONEBOT_WS_URL}")

    # 主动行为引擎作为后台任务
    proactive_task = asyncio.create_task(proactive.run_loop())
    logger.info("主动行为引擎已启动")

    # 定时清理任务（防止数据库无限膨胀）
    async def cleanup_loop():
        while True:
            try:
                await asyncio.sleep(config.CLEANUP_INTERVAL_HOURS * 3600)
                deleted = await memory.cleanup(
                    keep_days=config.HISTORY_KEEP_DAYS,
                    keep_per_session=config.HISTORY_KEEP_PER_SESSION,
                )
                logger.info(f"🗑️ 定时清理完成，删除 {deleted} 条过期消息")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"定时清理失败: {e}")

    cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info(f"定时清理已启动（每 {config.CLEANUP_INTERVAL_HOURS} 小时）")

    # 学习循环
    learning_task = None
    if config.LEARNING_ENABLED:
        learning_task = asyncio.create_task(learning.run_loop())

    try:
        await client.run_forever()
    finally:
        proactive_task.cancel()
        cleanup_task.cancel()
        if learning_task:
            learning_task.cancel()


def run():
    """同步入口（处理信号）"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop_event = asyncio.Event()

    def shutdown(sig):
        logging.getLogger("qqchat").info(f"收到信号 {sig}，正在退出...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: shutdown(s))
        except NotImplementedError:
            # Windows 不支持
            pass

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    run()

