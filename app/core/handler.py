"""消息处理器 —— 决定是否回复、组装上下文、调用 LLM、存记忆"""

import re
import time
import random
import logging

from ..config import config
from ..llm.prompt import PromptBuilder
from ..llm.tools import chat_with_tools
from ..core.reply import ReplyStrategy
from ..core.segments import describe_event, normalize_reply

logger = logging.getLogger(__name__)


class MessageHandler:
    def __init__(self, memory, llm, persona_manager):
        self.memory = memory
        self.llm = llm
        self.persona_manager = persona_manager
        self.tool_executor = None  # 由 main.py 注入（ToolExecutor）
        self.vision = None         # 由 main.py 注入（VisionClient）
        self.rate_limiter = None   # 由 main.py 注入（RateLimiter）
        self.serverops = None      # 由 main.py 注入（ServerOps）
        self.ops_session = None    # 由 main.py 注入（OpsSessionManager）
        self.reply_strategy = ReplyStrategy(
            delay_min=config.REPLY_DELAY_MIN,
            delay_max=config.REPLY_DELAY_MAX,
            split_enabled=config.SPLIT_REPLY,
            split_threshold=config.SPLIT_THRESHOLD,
        )
        self._profile_cooldown: dict[str, float] = {}

    # ---------- 事件解析 ----------

    def get_text(self, event: dict) -> str:
        """提取消息内容（文字 + 表情描述 + 图片占位）

        纯表情/纯图片消息也会返回可读内容，不再是空字符串。
        """
        text, _meta = describe_event(event, bot_name=self.persona_manager.current.name
                                     if self.persona_manager.current else "")
        return text

    def is_at_me(self, event: dict) -> bool:
        """是否 @ 了机器人"""
        self_id = str(event.get("self_id", ""))
        msg = event.get("message", "")
        if isinstance(msg, str):
            return f"[CQ:at,qq={self_id}]" in msg
        for seg in msg:
            if seg.get("type") == "at" and str(seg.get("data", {}).get("qq")) == self_id:
                return True
        return False

    def session_key(self, event: dict) -> str:
        """会话隔离键"""
        user_id = str(event.get("user_id", ""))
        if event.get("message_type") == "private":
            return f"private:{user_id}"
        return f"group:{event.get('group_id')}:{user_id}"

    def sender_name(self, event: dict) -> str:
        sender = event.get("sender", {}) or {}
        return sender.get("card") or sender.get("nickname") or f"用户{event.get('user_id')}"

    # ---------- 是否回复 ----------

    def should_reply(self, event: dict) -> bool:
        # 忽略自己发的消息
        if str(event.get("user_id")) == str(event.get("self_id")):
            return False

        if event.get("message_type") == "private":
            return True

        # 群聊
        if self.is_at_me(event):
            return True

        if config.GROUP_AT_ONLY:
            return False

        text = self.get_text(event)
        for trigger in config.GROUP_TRIGGERS:
            if trigger and trigger in text:
                return random.random() < config.GROUP_REPLY_PROB

        return False

    # ---------- 主流程 ----------

    async def handle(self, event: dict):
        """处理一条消息，返回是否已回复"""
        # 每条消息都记录类型，便于诊断（纯 ASCII，避免日志乱码）
        try:
            segs = [s.get("type") for s in event.get("message", []) if isinstance(s, dict)]
            logger.info(f"MSG recv type={event.get('message_type')} segs={segs} "
                        f"user={event.get('user_id')}")
        except Exception:
            pass

        if not self.should_reply(event):
            return False

        text = self.get_text(event)

        user_id = str(event.get("user_id", ""))
        nickname = self.sender_name(event)
        session_key = self.session_key(event)

        # 图片：主模型是多模态，直接把图片放进上下文（不绕"先描述"）
        _text2, meta = describe_event(event, bot_name="")
        images = meta.get("images") or []
        if meta.get("has_media"):
            # 诊断：把媒体段原始数据打出来，便于确认 NapCat 给了什么字段
            try:
                import json as _json
                raw_msg = event.get("message")
                logger.info(f"🖼️ 媒体段原始数据: {_json.dumps(raw_msg, ensure_ascii=False)[:600]}")
            except Exception:
                pass
        image_refs: list[str] = []
        if images and self.vision:
            try:
                image_refs = await self.vision.load_images(
                    images, max_count=config.VISION_MAX_IMAGES
                )
                if image_refs:
                    logger.info(f"👁️ 直接附上 {len(image_refs)} 张图片给多模态模型")
            except Exception as e:
                logger.warning(f"图片加载失败: {e}")

        # 图片没加载成功但有图：退回描述（保底）
        if images and not image_refs and self.vision and config.VISION_FALLBACK_DESCRIBE:
            try:
                descs = await self.vision.describe_images(images, max_count=config.VISION_MAX_IMAGES)
                if descs:
                    desc_text = "；".join(descs)
                    text = f"{text}\n【图片内容：{desc_text}】" if text else f"【图片内容：{desc_text}】"
            except Exception as e:
                logger.warning(f"图片描述失败: {e}")

        # 完全没有任何内容（极少数情况）才放弃
        if not text and not image_refs:
            logger.info(f"空消息，忽略: {meta}")
            return False

        # 引用消息：把被引用的原文查出来（很多客户端只给 message_id）
        replied_ids = meta.get("replied_ids") or []
        if replied_ids and self.tool_executor:
            try:
                extra = []
                for mid in replied_ids[:2]:
                    resp = await self.tool_executor._api("get_msg", {"message_id": int(mid)})
                    if not resp or resp.get("status") == "failed":
                        continue
                    md = resp.get("data") or {}
                    sender = (md.get("sender") or {})
                    sname = (sender.get("card") or sender.get("nickname")
                             or str(md.get("user_id", "")))
                    raw = md.get("raw_message") or ""
                    if not raw:
                        seg_parts = []
                        for sg in (md.get("message") or []):
                            if isinstance(sg, dict):
                                if sg.get("type") == "text":
                                    seg_parts.append(sg.get("data", {}).get("text", ""))
                                elif sg.get("type") == "image":
                                    seg_parts.append("[图片]")
                                elif sg.get("type") == "face":
                                    seg_parts.append("[表情]")
                        raw = "".join(seg_parts)
                    raw = (raw or "").strip()[:200]
                    if raw:
                        extra.append(f"{sname}: {raw}")
                if extra:
                    text = f"（对方在回复这条消息 → {' / '.join(extra)}）\n{text}"
                    logger.info(f"💬 引用消息已解析: {extra[0][:60]}")
            except Exception as e:
                logger.warning(f"引用消息解析失败: {e}")

        # 更新活跃度
        await self.memory.profile.touch(user_id, nickname)

        # 服务器操作（严格入口：管理员 + 私聊 + !! 前缀）
        ops_result = await self._handle_server_ops(event, text, user_id)
        if ops_result is not None:
            return ops_result

        # 命令
        cmd_result = await self._handle_command(text, session_key, user_id)
        if cmd_result is not None:
            return cmd_result

        # 组装上下文
        is_group = event.get("message_type") == "group"
        if is_group:
            # 群聊：用群公共上下文（能看到所有成员的发言）
            group_id = str(event.get("group_id", ""))
            history = await self.memory.conversation.get_group_history(
                group_id, config.HISTORY_LIMIT
            )
            summary_key = f"group:{group_id}"
        else:
            history = await self.memory.conversation.get_history(
                session_key, config.HISTORY_LIMIT
            )
            summary_key = session_key

        profile_text = await self.memory.profile.get_profile_text(user_id)
        msg_count = await self.memory.profile.msg_count(user_id)

        # 早前对话的压缩摘要（长对话不失忆）
        older_summary = await self.memory.conversation.get_summary(summary_key)

        # 结构化事实记忆 + 群内黑话
        facts = await self.memory.facts.get_facts(user_id, limit=8)
        terms = await self.memory.facts.get_terms(
            f"group:{event.get('group_id')}" if is_group else "global", limit=8
        )

        # 心情状态
        mood_hint = ""
        if getattr(self, "mood", None):
            mood_hint = self.mood.prompt_hint()
            # 心情事件：检测被夸/被怼
            if any(k in text for k in ("夸", "厉害", "可爱", "喜欢你", "真棒")):
                await self.mood.trigger("praised")
            if "胖" in text and "大肥鱼" in text:
                await self.mood.trigger("insulted")

        # 心情自动切换人格（生气→哦鲸鲸，困/无聊→小鲸鲸，其余→大肥鱼）
        persona = self.persona_manager.current
        if getattr(self, "mood", None) and config.MOOD_PERSONA_SWITCH:
            target = self.mood.persona_for_mood()
            current_key = getattr(self, "_persona_key", config.PERSONA)
            if target != current_key and self.persona_manager.set_current(target):
                self._persona_key = target
                logger.info(f"🎭 心情切换人格: {self.mood.current} → {target}")
                persona = self.persona_manager.current

        builder = PromptBuilder(persona.system_prompt, config.HISTORY_LIMIT)
        messages = builder.build(
            history=history,
            profile_text=profile_text,
            nickname=nickname,
            user_message=text,
            msg_count=msg_count,
            is_group=is_group,
            mood_hint=mood_hint,
            older_summary=older_summary,
            facts=facts,
            terms=terms,
            image_refs=image_refs,
        )

        # 调 LLM（带工具循环）
        if self.tool_executor:
            context = {
                "event": event,
                "group_id": str(event.get("group_id", "")),
                "user_id": user_id,
                "session_key": session_key,
            }
            reply = await chat_with_tools(
                self.llm, messages, self.tool_executor, context,
                max_rounds=config.TOOL_MAX_ROUNDS,
                temperature=persona.temperature,
                max_tokens=config.LLM_MAX_TOKENS,
            )
        else:
            reply = await self.llm.chat(
                messages,
                temperature=persona.temperature,
                max_tokens=config.LLM_MAX_TOKENS,
            )
        if not reply:
            logger.warning("LLM 无回复")
            return False

        # 输出归一化：把模型写的「[表情:可爱]」「[QQ表情#427]」变成真 CQ 码，
        # 清掉发不出去的占位符，避免用户看到字面量
        reply = normalize_reply(reply)
        if not reply:
            # 可能是被安全分类标记污染了正文，追加一句约束重试一次
            logger.warning("归一化后为空，重试一次")
            try:
                retry_msgs = list(messages) + [{
                    "role": "system",
                    "content": "只输出你要发给对方的那句话本身，不要输出任何标记、判定、解释。",
                }]
                reply = await self.llm.chat(
                    retry_msgs,
                    temperature=persona.temperature,
                    max_tokens=config.LLM_MAX_TOKENS,
                )
                reply = normalize_reply(reply or "")
            except Exception as e:
                logger.warning(f"重试失败: {e}")
        if not reply:
            logger.warning("LLM 无有效回复")
            return False

        # 愉快对话加心情
        if getattr(self, "mood", None) and random.random() < 0.3:
            await self.mood.trigger("good_chat")

        # 记录 token 消耗（给限流器算日预算）
        if getattr(self, "rate_limiter", None):
            prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
            self.rate_limiter.record(session_key,
                                     reply_chars=len(reply),
                                     prompt_chars=prompt_chars)

        # 存记忆
        await self.memory.conversation.add(session_key, user_id, nickname, "user", text)
        await self.memory.conversation.add(session_key, user_id, persona.name, "assistant", reply)

        # 更新画像
        await self._maybe_update_profile(user_id, session_key)

        # 长对话自动压缩（超出窗口的旧消息摘要成"更早的记忆"）
        await self._maybe_compress(
            summary_key=summary_key,
            is_group=is_group,
            group_id=str(event.get("group_id", "")) if is_group else "",
            session_key=session_key,
        )

        # 返回回复内容，由上层发送（便于测试）
        return reply

    # ---------- 上下文压缩 ----------

    async def _maybe_compress(self, summary_key: str, is_group: bool,
                              group_id: str, session_key: str):
        """当会话消息数超过阈值时，把滚出窗口的旧消息压缩成摘要"""
        try:
            threshold = config.HISTORY_LIMIT + config.COMPRESS_TRIGGER_EXTRA

            if is_group:
                total = await self.memory.conversation.count_group(group_id)
                if total < threshold:
                    return
                old = await self.memory.conversation.get_group_unsummarized(
                    group_id, keep_recent=config.HISTORY_LIMIT
                )
            else:
                total = await self.memory.conversation.count(session_key)
                if total < threshold:
                    return
                old = await self.memory.conversation.get_unsummarized(
                    session_key, keep_recent=config.HISTORY_LIMIT
                )

            if not old:
                return

            old_text = "\n".join(
                f"{'对方' if m['role'] == 'user' else '你'}"
                f"{'(' + m['nickname'] + ')' if m.get('nickname') else ''}: {m['content']}"
                for m in old
            )
            prev = await self.memory.conversation.get_summary(summary_key)
            new_summary = await self.llm.summarize_history(old_text, prev)
            if new_summary:
                await self.memory.conversation.set_summary(summary_key, new_summary)
                logger.info(f"📚 已压缩 {len(old)} 条旧消息为摘要（{len(new_summary)} 字）")
        except Exception as e:
            logger.warning(f"压缩上下文失败: {e}")

    async def _handle_server_ops(self, event: dict, text: str, user_id: str):
        """服务器操作（会话模式）

        - 口令进入：/help me for linux by ra 666
        - 模式内：每条消息当 bash 命令执行
        - 60 秒无操作自动退出；/exit help 手动退出
        - 仅管理员私聊生效，其他人/群聊完全不可见
        """
        if not self.ops_session:
            return None
        if event.get("message_type") != "private":
            return None

        result = await self.ops_session.handle(text, user_id, is_private=True)
        if result is not None:
            logger.info(f"🖥️ 服务器操作响应: {user_id}")
            try:
                await self.memory.facts.log("server_exec", f"{user_id}: {text[:150]}")
            except Exception:
                pass
        return result

    async def _handle_command(self, text: str, session_key: str, user_id: str):
        """处理命令，返回回复文本；非命令返回 None"""
        t = text.strip()

        if t in ("/reset", "/清空", "/清除记忆"):
            n = await self.memory.conversation.clear(session_key)
            return f"清掉了 {n} 条记录。" if n else "本来就没聊过什么。"

        if t in ("/profile", "/画像"):
            p = await self.memory.profile.get_profile_text(user_id)
            return f"我印象里的你：\n{p}" if p else "还没记住什么呢。"

        if t in ("/help", "/帮助"):
            return (
                "命令：\n"
                "/reset 清空对话记忆\n"
                "/profile 看用户画像\n"
                "/persona 看当前人格\n"
                "/persona set <名称> 切换人格（dafeiyu/xiaojingjing/ojingjing）\\n"
                "/mood 看心情\n"
                "/facts 看记住你哪些事\n"
                "/check 自检运行状态\n"
                "/learn 手动触发学习\n"
                "/help 帮助"
            )

        if t == "/persona" or t == "/人设":
            p = self.persona_manager.current
            return f"当前人格：{p.name}\n{p.description}" if p else "未加载人设"

        # 人格切换：/persona set xxx
        if t.startswith("/persona set") or t.startswith("/人设切换"):
            parts = t.split()
            if len(parts) < 3:
                all_p = self.persona_manager.list_all()
                names = "、".join(all_p.keys())
                return f"用法：/persona set <名称>\n可用：{names}"
            key = parts[2]
            if self.persona_manager.set_current(key):
                p = self.persona_manager.current
                # 持久化选择
                await self.memory.db.execute(
                    """INSERT INTO system_state (key, value) VALUES ('persona', ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                    (key,),
                )
                return f"人格已切换：{p.avatar} {p.name}\n{p.description}"
            return "没这个人格，/persona 看看有哪些"

        if t in ("/mood", "/心情"):
            if getattr(self, "mood", None):
                return self.mood.describe()
            return "心情系统未启用"

        if t in ("/check", "/自检"):
            if self.tool_executor:
                return await self.tool_executor.execute("bot_selfcheck", {}, {})
            return "自检工具未就绪"

        if t in ("/learn", "/学习"):
            if self.tool_executor:
                return await self.tool_executor.execute("bot_learn", {"target": "all"}, {})
            return "学习引擎未就绪"

        if t in ("/facts", "/事实"):
            facts = await self.memory.facts.get_facts(user_id, limit=15)
            if not facts:
                return "还没学到你的什么事实，多聊聊。"
            return "我记住的关于你的事：\n" + "\n".join(f"- {f}" for f in facts)

        return None

    async def _maybe_update_profile(self, user_id: str, session_key: str):
        """定期更新用户画像"""
        now = time.time()
        last = self._profile_cooldown.get(user_id, 0)
        if now - last < config.PROFILE_UPDATE_COOLDOWN:
            return

        if not await self.memory.profile.should_update_profile(
            user_id, config.PROFILE_UPDATE_INTERVAL
        ):
            return

        self._profile_cooldown[user_id] = now
        try:
            history = await self.memory.conversation.get_history(session_key, 30)
            convo = "\n".join(
                f"{'对方' if h['role'] == 'user' else '你'}: {h['content']}"
                for h in history
            )
            old = await self.memory.profile.get_profile_text(user_id)
            new_profile = await self.llm.summarize_profile(convo, old)
            if new_profile:
                await self.memory.profile.set_profile_text(user_id, new_profile)
                logger.info(f"已更新用户 {user_id} 画像")
        except Exception as e:
            logger.warning(f"更新画像失败: {e}")

