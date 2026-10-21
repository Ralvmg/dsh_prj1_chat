"""Web 聊天服务 —— 把现有的 QQChat 后端暴露成 HTTP API

复用现有组件：
- Memory（记忆）
- PersonaManager（人设）
- LLMClient（模型）
- ToolExecutor（33 个工具）
- MoodEngine（心情）

接口：
  GET  /api/chat/health         健康检查
  GET  /api/chat/personas       人设列表
  POST /api/chat/send           发消息 {session_id, message}
  GET  /api/chat/history?session_id=xxx   历史
  POST /api/chat/persona        切换人设 {session_id, persona}
"""

import asyncio
import json
import logging
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


class WebChatServer:
    def __init__(self, memory, llm, persona_manager, mood, tool_executor,
                 host="127.0.0.1", port=8080, tts=None):
        self.memory = memory
        self.llm = llm
        self.persona_manager = persona_manager
        self.mood = mood
        self.tool_executor = tool_executor
        self.tts = tts
        self.host = host
        self.port = port
        self._loop = None

    # ---------- 会话 ----------

    def session_key(self, sid: str) -> str:
        return f"web:{sid}"

    async def handle_chat(self, sid: str, message: str) -> dict:
        """处理一条聊天消息"""
        from ..core.handler import MessageHandler
        from ..llm.prompt import PromptBuilder
        from ..llm.tools import chat_with_tools
        from ..core.segments import normalize_reply
        from ..config import config

        user_id = f"web-{sid}"
        nickname = "访客"
        sk = self.session_key(sid)

        # 记录用户消息
        await self.memory.profile.touch(user_id, nickname)

        # 心情
        mood_hint = ""
        if self.mood:
            mood_hint = self.mood.prompt_hint()

        # 上下文
        history = await self.memory.conversation.get_history(sk, config.HISTORY_LIMIT)
        profile_text = await self.memory.profile.get_profile_text(user_id)
        msg_count = await self.memory.profile.msg_count(user_id)
        older_summary = await self.memory.conversation.get_summary(sk)
        facts = await self.memory.facts.get_facts(user_id, limit=8)

        persona = self.persona_manager.current
        builder = PromptBuilder(persona.system_prompt, config.HISTORY_LIMIT)
        messages = builder.build(
            history=history,
            profile_text=profile_text,
            nickname=nickname,
            user_message=message,
            msg_count=msg_count,
            mood_hint=mood_hint,
            older_summary=older_summary,
            facts=facts,
        )

        # 调 LLM（带工具）
        # channel=web：不把 QQ 类工具给模型（模型看不到就不会去调，也不会拿它当话题）
        ctx = {"event": {}, "group_id": "", "user_id": user_id,
               "session_key": sk, "channel": "web"}
        if self.tool_executor:
            from ..llm.tools import TOOLS
            web_tools = [
                t for t in TOOLS
                if not t["function"]["name"].startswith("qq_")
            ]
            # 完全没有工具时不要传空列表（部分模型会报错）
            if not web_tools:
                web_tools = None
            reply = await chat_with_tools(
                self.llm, messages, self.tool_executor, ctx,
                max_rounds=config.TOOL_MAX_ROUNDS,
                temperature=persona.temperature,
                max_tokens=config.LLM_MAX_TOKENS,
                tools=web_tools,
            )
        else:
            reply = await self.llm.chat(
                messages, temperature=persona.temperature,
                max_tokens=config.LLM_MAX_TOKENS,
            )

        if not reply:
            return {"ok": False, "error": "模型无响应"}

        reply = normalize_reply(reply)

        # Web 通道：把 CQ 表情码转成 emoji/图片，让网页能显示
        web_reply = self._cq_to_web(reply)

        # 语音合成（如果开启）
        audio_b64 = None
        if self.tts and self.tts.enabled:
            try:
                audio_b64 = await self.tts.synthesize(web_reply)
            except Exception as e:
                logger.warning(f"TTS 失败: {e}")

        # 存记忆（存原始带 CQ 码的，保持和 QQ 端一致）
        await self.memory.conversation.add(sk, user_id, nickname, "user", message)
        await self.memory.conversation.add(sk, user_id, persona.name, "assistant", reply)

        # 心情事件
        if self.mood:
            if any(k in message for k in ("夸", "厉害", "可爱", "喜欢你", "真棒")):
                await self.mood.trigger("praised")
            elif "胖" in message or "肥" in message:
                await self.mood.trigger("insulted")

        return {
            "ok": True,
            "reply": web_reply,
            "audio": audio_b64,          # base64 mp3，前端解码播放
            "persona": persona.name,
            "mood": self.mood.current if self.mood else "",
        }

    # ---------- CQ 码 → Web 可显示 ----------

    # QQ 表情 id → emoji 近似
    FACE_EMOJI = {
        0: "😲", 1: "😒", 2: "😍", 3: "😳", 4: "😎", 5: "😢", 6: "😊",
        7: "🤐", 8: "😴", 9: "😭", 10: "😅", 11: "😡", 12: "😜", 13: "😁",
        14: "🙂", 15: "😞", 16: "😎", 17: "😰", 18: "😫", 19: "🤮",
        20: "🤭", 21: "🥰", 22: "🙄", 23: "😤", 24: "🍚", 25: "😪",
        26: "😱", 27: "😓", 28: "😄", 29: "😌", 30: "💪", 31: "🤬",
        32: "❓", 33: "🤫", 34: "😵", 35: "😖", 36: "😩", 37: "💀",
        38: "🔨", 39: "👋", 40: "😅", 41: "🤏", 42: "👏", 43: "😅",
        44: "😏", 45: "😏", 46: "😏", 47: "🥱", 48: "🤨", 49: "🥺",
        50: "🥹", 51: "😈", 52: "😘", 53: "😨", 54: "🥺", 55: "🔪",
        56: "🍉", 57: "🍺", 58: "🏀", 59: "🏓", 60: "☕", 61: "🍚",
        62: "🐷", 63: "🌹", 64: "🥀", 65: "❤️", 66: "❤️", 67: "💔",
        68: "🎂", 69: "⚡", 70: "💣", 71: "🔪", 72: "⚽", 73: "🐞",
        74: "💩", 75: "🌙", 76: "☀️", 77: "🎁", 78: "🤗", 79: "👍",
        80: "👎", 81: "🤝", 82: "✌️", 83: "👊", 84: "💃", 85: "👊",
        86: "👎", 87: "😘", 88: "🙅", 89: "👌", 90: "💕", 91: "😘",
        92: "🕺", 93: "😨", 94: "💔", 95: "🌀", 96: "🙇", 97: "🔙",
        98: "🤸", 99: "👋", 100: "🎉", 101: "🕺", 102: "😘", 103: "☯️",
        104: "☯️", 105: "😄", 106: "🎊", 107: "🧨", 108: "🏮",
        109: "🎤", 110: "🛍️", 111: "📧", 112: "🕶️", 113: "👏",
        114: "🙏", 115: "💢", 116: "🍭", 117: "🍼", 118: "🍜",
        119: "🍌", 120: "✈️", 121: "🚗", 122: "🚄", 123: "🚃",
        124: "🚃", 125: "🚃", 126: "☁️", 127: "🌧️", 128: "💵",
        129: "🐼", 130: "💡", 131: "🎡", 132: "⏰", 133: "☂️",
        134: "🎈", 135: "💍", 136: "🛋️", 137: "🧻",
        172: "🤔", 173: "😉", 174: "😭", 175: "😑", 176: "🥺",
        177: "😖", 178: "😏", 179: "🐶", 180: "😲", 181: "😠",
        182: "😂", 183: "💄", 184: "🦀", 185: "🦙", 186: "🌰",
        187: "👻", 188: "🥚", 189: "🔞", 190: "🌼", 191: "👽",
        192: "🚂",
    }

    def _cq_to_web(self, text: str) -> str:
        """把 CQ 码转成网页能渲染的内容

        - [CQ:face,id=X]  → emoji（如 [CQ:face,id=21] → 🥰）
        - [CQ:image,file=URL] → 保留（前端渲染成 <img>）
        - 其它 CQ 码 → 移除
        """
        import re

        def repl(m):
            cq = m.group(1)
            parts = cq.split(",")
            typ = parts[0]
            kv = {}
            for p in parts[1:]:
                if "=" in p:
                    k, _, v = p.partition("=")
                    kv[k] = v

            if typ == "face":
                try:
                    fid = int(kv.get("id", 0))
                except ValueError:
                    fid = 0
                return self.FACE_EMOJI.get(fid, "🙂")
            if typ == "image":
                url = kv.get("url") or kv.get("file") or ""
                if url.startswith("http") or url.startswith("data:"):
                    return f"[CQ:image,file={url}]"   # 前端处理
                return ""
            # 其它（at/poke/reply 等）网页端不显示
            return ""

        return re.sub(r"\[CQ:([^\]]+)\]", repl, text).strip()


    async def handle_history(self, sid: str) -> dict:
        sk = self.session_key(sid)
        rows = await self.memory.conversation.get_history(sk, 50)
        return {
            "ok": True,
            "messages": [
                {"role": r["role"], "content": r["content"]}
                for r in rows
            ],
        }

    async def handle_clear(self, sid: str) -> int:
        """清空会话历史（含摘要），返回删除条数

        人设规则更新后，旧回复仍留在历史里会让模型照抄，
        所以提供这个接口清掉污染。
        """
        sk = self.session_key(sid)
        n = await self.memory.conversation.clear(sk)
        try:
            await self.memory.conversation.set_summary(sk, "")
        except Exception:
            pass
        return n

    # ---------- HTTP ----------

    def make_handler(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _json(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _run_async(self, coro):
                """在线程里跑 async 函数"""
                try:
                    return asyncio.run_coroutine_threadsafe(coro, srv._loop).result(timeout=120)
                except Exception as e:
                    logger.exception("async 执行失败")
                    raise

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                u = urlparse(self.path)

                # ---------- 项目信息（对外入口）----------
                if u.path in ("/api/chat", "/api/chat/"):
                    return self._json(200, {
                        "ok": True,
                        "project": "鲸鱼娘 Chat",
                        "description": "会聊天、有记忆、能看图的 AI 角色机器人",
                        "repo": "https://github.com/Ralvmg/dsh_prj1_chat",
                        "endpoints": {
                            "health": "GET  /api/chat/health",
                            "personas": "GET  /api/chat/personas",
                            "history": "GET  /api/chat/history?session_id=xxx",
                            "send": "POST /api/chat/send",
                            "switch_persona": "POST /api/chat/persona",
                        },
                        "credits": "dsh-pet-indesktop / NapCatQQ / AstrBot / edge-tts",
                    })

                if u.path == "/api/chat/health":
                    return self._json(200, {
                        "ok": True,
                        "persona": srv.persona_manager.current.name if srv.persona_manager.current else "",
                        "persona_key": srv.persona_manager.current_key,
                        "mood": srv.mood.current if srv.mood else "",
                        "version": "1.0.0",
                    })
                if u.path == "/api/chat/personas":
                    all_p = srv.persona_manager.list_all()
                    cur_key = srv.persona_manager.current_key if hasattr(
                        srv.persona_manager, "current_key") else ""
                    return self._json(200, {
                        "ok": True,
                        "current": srv.persona_manager.current.name if srv.persona_manager.current else "",
                        "current_key": cur_key,
                        "personas": [
                            {"key": k, "name": v["name"], "avatar": v.get("avatar", ""),
                             "description": v.get("description", "")}
                            for k, v in all_p.items()
                        ],
                    })
                if u.path == "/api/chat/history":
                    qs = parse_qs(u.query)
                    sid = (qs.get("session_id") or ["default"])[0]
                    try:
                        r = self._run_async(srv.handle_history(sid))
                        return self._json(200, r)
                    except Exception as e:
                        return self._json(500, {"ok": False, "error": str(e)})
                return self._json(404, {"ok": False, "error": "not found"})

            def do_DELETE(self):
                """清空某个会话的历史（人设更新后清理污染用）"""
                u = urlparse(self.path)
                if u.path == "/api/chat/history":
                    qs = parse_qs(u.query)
                    sid = (qs.get("session_id") or [""])[0]
                    if not sid:
                        return self._json(400, {"ok": False, "error": "missing session_id"})
                    try:
                        n = self._run_async(srv.handle_clear(sid))
                        logger.info(f"🧹 已清空会话历史: web:{sid}（{n} 条）")
                        return self._json(200, {"ok": True, "deleted": n})
                    except Exception as e:
                        return self._json(500, {"ok": False, "error": str(e)})
                return self._json(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                u = urlparse(self.path)
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception as e:
                    return self._json(400, {"ok": False, "error": f"bad json: {e}"})

                if u.path == "/api/chat/send":
                    sid = str(body.get("session_id") or "default")
                    msg = str(body.get("message") or "").strip()
                    if not msg:
                        return self._json(400, {"ok": False, "error": "empty message"})
                    try:
                        r = self._run_async(srv.handle_chat(sid, msg))
                        return self._json(200, r)
                    except Exception as e:
                        logger.exception("chat failed")
                        return self._json(500, {"ok": False, "error": str(e)})

                if u.path == "/api/chat/persona":
                    key = str(body.get("persona") or "").strip()
                    if not key:
                        return self._json(400, {"ok": False, "error": "missing persona"})
                    ok = srv.persona_manager.set_current(key)
                    return self._json(200, {
                        "ok": ok,
                        "current": srv.persona_manager.current.name if ok else "",
                    })

                return self._json(404, {"ok": False, "error": "not found"})

            def log_message(self, fmt, *args):
                logger.debug("web: " + fmt % args)

        return Handler

    def serve(self, loop):
        """在后台线程启动 HTTP 服务

        loop: 主事件循环（由 main.py 传入）—— 所有 async 操作必须提交到它，
              不能新建 loop，否则跨 loop 调用数据库/httpx 会死锁。
        """
        self._loop = loop
        handler = self.make_handler()
        srv = ThreadingHTTPServer((self.host, self.port), handler)
        logger.info(f"WebChat HTTP 服务启动: http://{self.host}:{self.port}")
        srv.serve_forever()

