"""工具系统 —— 让机器人能调用 OneBot API 和服务器能力（轻量版 DSH 理念）

工具分两类：
1. QQ 工具：查群信息、群成员、好友列表、改在线状态、撤回消息等（走 OneBot API）
2. 服务器工具：查自己的运行状态（心情、记忆量、运行时长）

实现方式：OpenAI function calling。
LLM 决定调用哪个工具 → 执行 → 结果回传 → LLM 组织语言回复。
"""

import json
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------- 工具定义（OpenAI 格式）----------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "qq_group_info",
            "description": "获取 QQ 群的详细信息：群名、成员数、群主、我的群名片等",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string", "description": "群号；不填默认当前群"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_group_members",
            "description": "获取 QQ 群成员列表：昵称、群名片、角色（群主/管理/成员）",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string", "description": "群号"},
                },
                "required": ["group_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_friend_list",
            "description": "获取好友列表：昵称、备注",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_set_status",
            "description": "设置我的 QQ 在线状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["online", "away", "invisible", "busy", "dnd"],
                        "description": "online=在线 away=离开 invisible=隐身 busy=忙碌 dnd=勿扰",
                    },
                },
                "required": ["status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_group_sign",
            "description": "在群里打卡签到",
            "parameters": {
                "type": "object",
                "properties": {"group_id": {"type": "string", "description": "群号"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bot_mood",
            "description": "查看或调整我自己的心情状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "boost", "calm"],
                        "description": "get=查看 boost=加能量 calm=冷静下来",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索。用于查事实、新闻、教程、梗的出处等你不确定的信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页正文内容（需要知道具体 URL 时用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http/https 地址"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hot_topics",
            "description": "查看当前热点（HN/V2EX/GitHub/B站/微博），用于跟人聊当天新鲜事、接梗",
            "parameters": {
                "type": "object",
                "properties": {
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选：hn/v2ex/github/bilibili/weibo，不填则全部",
                    },
                    "limit": {"type": "integer", "description": "每个源取几条，默认 5"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_save",
            "description": "把一条值得长期记住的知识存进知识库（别人教你的、你查证过的结论）",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "主题关键词"},
                    "content": {"type": "string", "description": "知识内容，一两句话"},
                    "source": {"type": "string", "description": "来源（URL 或'XX告诉我'）"},
                },
                "required": ["topic", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "在知识库里检索之前存过的知识",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_group_history",
            "description": "拉取某个群最近的聊天记录（用于了解群里之前聊了什么、补齐上下文）",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string", "description": "群号；不填则当前群"},
                    "count": {"type": "integer", "description": "条数，默认 15，最多 40"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_group_list",
            "description": "我加入的所有群列表（群号、群名、人数）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_member_info",
            "description": "查某个群成员的详细信息（群名片、昵称、角色、入群时间、QQ号）",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "目标 QQ 号"},
                    "group_id": {"type": "string", "description": "群号；不填则当前群"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_stranger_info",
            "description": "查一个陌生人/非好友的公开资料（昵称、性别、年龄）",
            "parameters": {
                "type": "object",
                "properties": {"user_id": {"type": "string", "description": "QQ 号"}},
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_msg_detail",
            "description": "按消息ID查一条消息的完整内容（用于理解引用、追溯上下文）",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"type": "string", "description": "消息 ID"}},
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_group_notice",
            "description": "查群公告",
            "parameters": {
                "type": "object",
                "properties": {"group_id": {"type": "string", "description": "群号；不填当前群"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_essence_list",
            "description": "查群精华消息",
            "parameters": {
                "type": "object",
                "properties": {"group_id": {"type": "string", "description": "群号；不填当前群"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_recent_contact",
            "description": "最近联系人列表（最近跟谁聊过）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_forward_msg",
            "description": "展开一条合并转发消息的内容",
            "parameters": {
                "type": "object",
                "properties": {"forward_id": {"type": "string", "description": "转发消息 ID"}},
                "required": ["forward_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_send_at",
            "description": "在群里 @ 某个人（单独发一条 @ 消息）",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "要 @ 的 QQ 号"},
                    "text": {"type": "string", "description": "附加文字，可空"},
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_recall",
            "description": "撤回自己刚发的一条消息",
            "parameters": {
                "type": "object",
                "properties": {"message_id": {"type": "string", "description": "消息 ID"}},
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_send_animation",
            "description": "发送鲸鱼娘自己的动画（开心/生气/害羞/困/吃饭等 20 种），用来表达情绪",
            "parameters": {
                "type": "object",
                "properties": {
                    "emotion": {
                        "type": "string",
                        "description": "情绪或场景：happy/angry/shy/laugh/wave/scared/frustrated/"
                                       "sleep/doze/yawn/stretch/idle/thinking/coding/humming/"
                                       "eat_rice/eat_token/bubble/tail_slap/caught",
                    },
                },
                "required": ["emotion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_send_sticker",
            "description": "发送鲸鱼娘表情包（招牌/胖鱼/可爱/哈基米/死鱼/没钱/去AGI等 22 种）",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "表情名：main/fat/cute/hajimi/dead/deepsleep/curse/agi/"
                                       "nomoney/push/quiet/mama/eval/uneval/update/virus/"
                                       "yupian/kaidian/multimodal/boss/bigcoming/dsh",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bot_selfcheck",
            "description": "自检：检查自己的运行状态（连接、数据库、记忆量、LLM 延迟、报错情况）。感觉不对或别人问「你还好吗」时用",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_llm_ping": {
                        "type": "boolean",
                        "description": "是否测试 LLM 响应延迟（会消耗极少量额度）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bot_memory_stats",
            "description": "查看我的记忆情况：认识多少人、聊了多少条消息、学到多少事实",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bot_learn",
            "description": "手动触发一次学习：从最近的对话里提取用户事实和群内黑话",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["facts", "terms", "all"],
                        "description": "facts=学用户事实 terms=学群黑话 all=都学",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_face_search",
            "description": "按情绪/关键词搜索 QQ 表情，返回可用的表情 id。想发表情但不确定 id 时先用这个查",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "关键词，如 开心、难过、生气、可爱、无语、困、赞、爱心",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_send_face",
            "description": "单独发一个 QQ 表情（作为一条独立消息发出去）",
            "parameters": {
                "type": "object",
                "properties": {
                    "face_id": {"type": "integer", "description": "表情 id，如 178 是斜眼笑"},
                },
                "required": ["face_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_send_image",
            "description": "发送一张网络图片（必须是可公开访问的 http/https 图片 URL）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "图片 URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qq_poke",
            "description": "戳一戳对方（QQ 的拍一拍）",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "要戳的 QQ 号；不填戳当前对话的人"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "server_time",
            "description": "获取当前服务器时间",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# 工具名集合（快速判断）
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


class ToolExecutor:
    """工具执行器"""

    def __init__(self, onebot_client, memory, mood_engine=None):
        self.client = onebot_client
        self.memory = memory
        self.mood = mood_engine
        self._started_at = time.time()
        # 由 main.py 注入，供自检/学习工具使用
        self.llm = None
        self.vision = None
        self.learning = None
        self.web = None
        self.persona_manager = None
        self.rate_limiter = None
        self.media = None          # 由 main.py 注入（MediaAssets）
        self.tool_executor_ready = True

    async def execute(self, name: str, arguments: dict, context: dict) -> str:
        """执行工具，返回文本结果（给 LLM 看）"""
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if not handler:
                return f"错误：未知工具 {name}"
            # 网页会话 / QQ 掉线时，QQ 类工具直接给友好提示，不用抛异常
            if name.startswith("qq_") and self._qq_blocked(context):
                return self.QQ_UNAVAILABLE_MSG
            return await handler(arguments, context)
        except Exception as e:
            logger.exception(f"工具 {name} 执行失败")
            return f"工具执行出错: {e}"

    # ---------- QQ 工具 ----------

    # QQ 服务不可用时的友好提示（网页端 / QQ 掉线时）
    QQ_UNAVAILABLE_MSG = (
        "QQ 服务当前不可用（未登录或已断线），这个功能暂时用不了。"
        "网页聊天里可以直接聊天、搜索、看热点、查时间，但查群/查人/发表情这类"
        "需要 QQ 的工具没法用。"
    )

    def _qq_blocked(self, ctx: dict) -> bool:
        """当前上下文是否不能使用 QQ 工具（网页会话、或 OneBot 未连接）"""
        if ctx.get("channel") == "web":
            return True
        if getattr(self.client, "connected", False) is False:
            return True
        return False

    async def _api(self, action: str, params: dict = None, timeout: float = 8.0):
        """调用 OneBot API 并等待结果"""
        try:
            echo = await self.client.send_api(action, params or {})
        except Exception as e:
            logger.warning(f"OneBot 不可用（{action}）: {e}")
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 从事件流里等响应（onebot.py 需要把响应放进队列）
            resp = await self.client.wait_response(echo, timeout=2.0)
            if resp is not None:
                return resp
        return None

    async def _tool_qq_group_info(self, args: dict, ctx: dict) -> str:
        gid = args.get("group_id") or ctx.get("group_id")
        if not gid:
            return "错误：不在群里，也没有指定群号"
        resp = await self._api("get_group_info", {"group_id": int(gid)})
        if not resp or resp.get("status") == "failed":
            return "获取群信息失败"
        d = resp.get("data", {})
        return (
            f"群信息：群名「{d.get('group_name')}」，群号 {d.get('group_id')}，"
            f"成员 {d.get('member_count')} 人，群上限 {d.get('max_member_count')} 人"
        )

    async def _tool_qq_group_members(self, args: dict, ctx: dict) -> str:
        gid = args.get("group_id") or ctx.get("group_id")
        if not gid:
            return "错误：需要群号"
        resp = await self._api("get_group_member_list", {"group_id": int(gid)})
        if not resp or resp.get("status") == "failed":
            return "获取群成员失败"
        members = resp.get("data") or []
        if not members:
            return "群成员列表为空"
        role_map = {"owner": "群主", "admin": "管理", "member": ""}
        lines = []
        for m in members[:100]:  # 最多 100 个
            card = m.get("card") or m.get("nickname") or ""
            role = role_map.get(m.get("role"), "")
            tag = f"({role})" if role else ""
            lines.append(f"{card}{tag}")
        more = f" …等共 {len(members)} 人" if len(members) > 100 else ""
        return f"群成员：{'、'.join(lines)}{more}"

    async def _tool_qq_friend_list(self, args: dict, ctx: dict) -> str:
        resp = await self._api("get_friend_list")
        if not resp or resp.get("status") == "failed":
            return "获取好友列表失败"
        friends = resp.get("data") or []
        names = [f.get("remark") or f.get("nickname") or "" for f in friends[:50]]
        more = f" …等共 {len(friends)} 人" if len(friends) > 50 else ""
        return f"好友：{'、'.join(n for n in names if n)}{more}" if names else "还没有好友"

    async def _tool_qq_set_status(self, args: dict, ctx: dict) -> str:
        status = args.get("status", "online")
        resp = await self._api("set_online_status", {"status": status})
        # NapCat 对此 API 支持不一，失败就如实说
        if resp and resp.get("status") != "failed":
            return f"已把在线状态改为 {status}"
        return f"状态切换没成功（{status}），可能这个接口不支持"

    async def _tool_qq_group_sign(self, args: dict, ctx: dict) -> str:
        gid = args.get("group_id") or ctx.get("group_id")
        if not gid:
            return "错误：需要群号"
        resp = await self._api("send_group_sign", {"group_id": int(gid)})
        if resp and resp.get("status") != "failed":
            return f"已在群 {gid} 打卡"
        return "打卡没成功，可能接口不支持"

    # ---------- 表情 / 图片 ----------

    async def _tool_qq_face_search(self, args: dict, ctx: dict) -> str:
        """按关键词搜索表情 id"""
        from ..core.segments import QQ_FACES, FACE_ALIASES, FACE_EMOTION_HINTS
        kw = (args.get("keyword") or "").strip()
        if not kw:
            return "请给个关键词"

        hits = []
        # 别名精确匹配
        if kw in FACE_ALIASES:
            hits.append((FACE_ALIASES[kw], kw))
        # 名称包含
        for fid, name in QQ_FACES.items():
            if kw in name and (fid, name) not in hits:
                hits.append((fid, name))
        # 情绪提示包含
        for fid, name in QQ_FACES.items():
            hint = FACE_EMOTION_HINTS.get(name, "")
            if kw in hint and (fid, name) not in hits:
                hits.append((fid, name))

        if not hits:
            return f"没找到跟「{kw}」相关的表情，可以试试：开心/可爱/难过/生气/无语/困/赞/爱心"

        lines = [f"{name}={fid}" for fid, name in hits[:10]]
        return f"「{kw}」相关的表情：{'、'.join(lines)}\n用法：把 [CQ:face,id=数字] 直接写进回复里"

    async def _tool_qq_send_face(self, args: dict, ctx: dict) -> str:
        fid = int(args.get("face_id") or 0)
        if not fid:
            return "需要表情 id"
        ok = await self.client.reply_raw(
            ctx.get("event") or {},
            f"[CQ:face,id={fid}]",
        )
        return f"表情已发出（id={fid}）" if ok else "表情没发出去"

    async def _tool_qq_send_image(self, args: dict, ctx: dict) -> str:
        url = (args.get("url") or "").strip()
        if not url.startswith("http"):
            return "需要可公开访问的 http/https 图片 URL"
        ok = await self.client.reply_raw(
            ctx.get("event") or {},
            f"[CQ:image,file={url}]",
        )
        return "图片已发出" if ok else "图片没发出去（可能 URL 不可访问或平台拦截）"

    async def _tool_qq_poke(self, args: dict, ctx: dict) -> str:
        uid = args.get("user_id") or ctx.get("user_id")
        gid = ctx.get("group_id")
        if gid:
            resp = await self._api("group_poke", {"group_id": int(gid), "user_id": int(uid)})
        else:
            resp = await self._api("friend_poke", {"user_id": int(uid)})
        return "戳了对方一下" if resp and resp.get("status") != "failed" else "戳一戳失败"

    # ---------- 联网 ----------

    async def _tool_web_search(self, args: dict, ctx: dict) -> str:
        if not self.web:
            return "联网能力未启用"
        q = (args.get("query") or "").strip()
        if not q:
            return "需要搜索关键词"
        limit = int(args.get("limit") or 5)
        results = await self.web.search(q, limit=limit)
        if not results:
            return f"没搜到「{q}」的结果（可能网络受限）"
        lines = []
        for i, r in enumerate(results, 1):
            s = f"{i}. {r['title']}"
            if r.get("snippet"):
                s += f" — {r['snippet']}"
            s += f"\n   {r['url']}"
            lines.append(s)
        return f"「{q}」搜索结果：\n" + "\n".join(lines)

    async def _tool_web_fetch(self, args: dict, ctx: dict) -> str:
        if not self.web:
            return "联网能力未启用"
        url = (args.get("url") or "").strip()
        if not url.startswith("http"):
            return "需要 http/https URL"
        text = await self.web.fetch(url, max_chars=2500)
        return f"网页正文：\n{text}"

    async def _tool_hot_topics(self, args: dict, ctx: dict) -> str:
        if not self.web:
            return "联网能力未启用"
        sources = args.get("sources") or None
        limit = int(args.get("limit") or 5)
        data = await self.web.hot_topics(sources=sources, limit=limit)
        if not data:
            return "暂时抓不到热点（网络或接口受限）"
        lines = []
        for src, items in data.items():
            lines.append(f"【{src}】" + "；".join(items))
        return "当前热点：\n" + "\n".join(lines)

    # ---------- 知识库 ----------

    async def _tool_kb_save(self, args: dict, ctx: dict) -> str:
        topic = (args.get("topic") or "").strip()
        content = (args.get("content") or "").strip()
        source = (args.get("source") or "").strip()[:200]
        if not topic or not content:
            return "需要 topic 和 content"
        now = int(time.time())
        await self.memory.db.execute(
            """INSERT INTO knowledge (topic, content, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (topic[:100], content[:1000], source, now, now),
        )
        return f"已记住「{topic}」"

    async def _tool_kb_search(self, args: dict, ctx: dict) -> str:
        q = (args.get("query") or "").strip()
        if not q:
            return "需要关键词"
        rows = await self.memory.db.fetchall(
            """SELECT topic, content, source FROM knowledge
               WHERE topic LIKE ? OR content LIKE ?
               ORDER BY hit_count DESC, updated_at DESC LIMIT 5""",
            (f"%{q}%", f"%{q}%"),
        )
        if not rows:
            return f"知识库里没有「{q}」相关记录"
        lines = [f"- {r['topic']}：{r['content']}" for r in rows]
        return f"知识库检索「{q}」：\n" + "\n".join(lines)

    # ---------- 群 / 成员 / 消息详情 ----------

    async def _tool_qq_group_list(self, args: dict, ctx: dict) -> str:
        resp = await self._api("get_group_list")
        if not resp or resp.get("status") == "failed":
            return "获取群列表失败"
        groups = resp.get("data") or []
        if not groups:
            return "还没加入任何群"
        lines = [f"{g.get('group_name')}({g.get('group_id')}) {g.get('member_count')}人"
                 for g in groups[:30]]
        return "我加入的群：\n" + "\n".join(lines)

    async def _tool_qq_member_info(self, args: dict, ctx: dict) -> str:
        uid = str(args.get("user_id") or "").strip()
        gid = args.get("group_id") or ctx.get("group_id")
        if not uid:
            return "需要 QQ 号"
        if not gid:
            return "需要群号（私聊里查群成员必须指定群）"
        resp = await self._api("get_group_member_info",
                               {"group_id": int(gid), "user_id": int(uid), "no_cache": False})
        if not resp or resp.get("status") == "failed":
            return "查不到这个成员（可能不在群里）"
        d = resp.get("data") or {}
        role = {"owner": "群主", "admin": "管理员", "member": "成员"}.get(d.get("role"), "")
        return (f"{d.get('card') or d.get('nickname')}（QQ:{d.get('user_id')}）\n"
                f"昵称：{d.get('nickname')}\n角色：{role}\n"
                f"群名片：{d.get('card') or '无'}\n"
                f"加群时间：{d.get('join_time')}")

    async def _tool_qq_stranger_info(self, args: dict, ctx: dict) -> str:
        uid = str(args.get("user_id") or "").strip()
        if not uid:
            return "需要 QQ 号"
        resp = await self._api("get_stranger_info", {"user_id": int(uid), "no_cache": False})
        if not resp or resp.get("status") == "failed":
            return "查不到这个人的资料"
        d = resp.get("data") or {}
        sex = {"male": "男", "female": "女", "unknown": "未知"}.get(d.get("sex"), "")
        return f"{d.get('nickname')}（QQ:{d.get('user_id')}）{sex} {d.get('age')}岁"

    async def _tool_qq_msg_detail(self, args: dict, ctx: dict) -> str:
        mid = str(args.get("message_id") or "").strip()
        if not mid:
            return "需要消息 ID"
        resp = await self._api("get_msg", {"message_id": int(mid)})
        if not resp or resp.get("status") == "failed":
            return "查不到这条消息"
        d = resp.get("data") or {}
        sender = d.get("sender") or {}
        sname = sender.get("card") or sender.get("nickname") or str(d.get("user_id"))
        raw = d.get("raw_message") or ""
        return f"发送者：{sname}（QQ:{d.get('user_id')}）\n时间：{d.get('time')}\n内容：{raw[:400]}"

    async def _tool_qq_group_notice(self, args: dict, ctx: dict) -> str:
        gid = args.get("group_id") or ctx.get("group_id")
        if not gid:
            return "需要群号"
        resp = await self._api("_get_group_notice", {"group_id": int(gid)})
        if not resp or resp.get("status") == "failed":
            return "获取群公告失败（可能没有公告或接口不支持）"
        notices = resp.get("data") or []
        if not notices:
            return "这个群没有公告"
        lines = []
        for n in notices[:5]:
            sender = (n.get("sender_id") or "")
            content = ""
            for seg in (n.get("message") or {}).get("message", []):
                if isinstance(seg, dict) and seg.get("type") == "text":
                    content += seg.get("data", {}).get("text", "")
            if content:
                lines.append(f"· {content[:150]}")
        return "群公告：\n" + "\n".join(lines) if lines else "群公告解析失败"

    async def _tool_qq_essence_list(self, args: dict, ctx: dict) -> str:
        gid = args.get("group_id") or ctx.get("group_id")
        if not gid:
            return "需要群号"
        resp = await self._api("get_essence_msg_list", {"group_id": int(gid)})
        if not resp or resp.get("status") == "failed":
            return "获取精华消息失败"
        items = resp.get("data") or []
        if not items:
            return "这个群没有精华消息"
        lines = []
        for it in items[:5]:
            content = (it.get("content") or "")[:100]
            if content:
                lines.append(f"· {content}")
        return "群精华：\n" + "\n".join(lines) if lines else "精华消息为空"

    async def _tool_qq_recent_contact(self, args: dict, ctx: dict) -> str:
        resp = await self._api("get_recent_contact", {})
        if not resp or resp.get("status") == "failed":
            return "获取最近联系人失败"
        items = resp.get("data") or []
        if not items:
            return "没有最近联系人"
        lines = []
        for it in items[:10]:
            chat = it.get("chatInfo") or {}
            name = chat.get("remark") or chat.get("nickname") or str(chat.get("user_id") or chat.get("group_id"))
            lines.append(f"· {name}")
        return "最近联系过：\n" + "\n".join(lines)

    async def _tool_qq_forward_msg(self, args: dict, ctx: dict) -> str:
        fid = str(args.get("forward_id") or "").strip()
        if not fid:
            return "需要转发消息 ID"
        resp = await self._api("get_forward_msg", {"id": fid})
        if not resp or resp.get("status") == "failed":
            return "展开转发消息失败"
        msgs = resp.get("data") or {}
        if isinstance(msgs, dict):
            msgs = msgs.get("messages") or []
        lines = []
        for m in msgs[:10]:
            sender = (m.get("sender") or {}).get("nickname") or ""
            text = ""
            for seg in (m.get("message") or []):
                if isinstance(seg, dict) and seg.get("type") == "text":
                    text += seg.get("data", {}).get("text", "")
            if text:
                lines.append(f"{sender}: {text[:80]}")
        return "转发内容：\n" + "\n".join(lines) if lines else "转发内容为空"

    async def _tool_qq_send_at(self, args: dict, ctx: dict) -> str:
        uid = str(args.get("user_id") or "").strip()
        text = (args.get("text") or "").strip()
        gid = ctx.get("group_id")
        if not uid:
            return "需要 QQ 号"
        if not gid:
            return "只能在群里 @ 人"
        msg = f"[CQ:at,qq={uid}] {text}".strip()
        ok = await self.client.reply_raw(ctx.get("event") or {}, msg)
        return "已 @" if ok else "@ 失败"

    async def _tool_qq_recall(self, args: dict, ctx: dict) -> str:
        mid = str(args.get("message_id") or "").strip()
        if not mid:
            return "需要消息 ID"
        resp = await self._api("delete_msg", {"message_id": int(mid)})
        return "已撤回" if resp and resp.get("status") != "failed" else "撤回失败（可能超过2分钟）"

    async def _tool_qq_send_animation(self, args: dict, ctx: dict) -> str:
        """发鲸鱼娘动画 GIF"""
        if not self.media:
            return "动画资源未加载"
        key = (args.get("emotion") or "").strip()
        path = self.media.get_animation(key)
        if not path:
            return (f"没有「{key}」这个动画。可用：{', '.join(self.media.list_animations())}")
        url = self.media.to_data_url(path)
        if not url:
            return "动画文件读取失败"
        ok = await self.client.reply_raw(
            ctx.get("event") or {}, f"[CQ:image,file={url}]"
        )
        return f"动画已发送（{key}）" if ok else "动画发送失败"

    async def _tool_qq_send_sticker(self, args: dict, ctx: dict) -> str:
        """发表情包"""
        if not self.media:
            return "表情资源未加载"
        key = (args.get("name") or "").strip()
        path = self.media.get_sticker(key)
        if not path:
            return (f"没有「{key}」这个表情包。可用：{', '.join(self.media.list_stickers())}")
        url = self.media.to_data_url(path)
        if not url:
            return "表情文件过大或读取失败"
        ok = await self.client.reply_raw(
            ctx.get("event") or {}, f"[CQ:image,file={url}]"
        )
        return f"表情包已发送（{key}）" if ok else "表情包发送失败"

    # ---------- 服务器工具 ----------

    async def _tool_bot_mood(self, args: dict, ctx: dict) -> str:
        if not self.mood:
            return "心情系统未启用"
        action = args.get("action", "get")
        if action == "get":
            return self.mood.describe()
        if action == "boost":
            await self.mood.trigger("praised")
            return self.mood.describe()
        if action == "calm":
            await self.mood.trigger("ignored")
            return self.mood.describe()
        return self.mood.describe()

    async def _tool_bot_memory_stats(self, args: dict, ctx: dict) -> str:
        total = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM messages")
        users = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM user_profile")
        facts = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM user_facts")
        terms = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM learned_terms")
        summaries = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM conversation_summary")
        groups = await self.memory.db.fetchone(
            """SELECT COUNT(DISTINCT substr(session_key, 7, instr(substr(session_key,7),':')-1)) AS n
               FROM messages WHERE session_key LIKE 'group:%:%'"""
        )
        uptime_min = int((time.time() - self._started_at) / 60)
        return (
            f"记忆统计：{total['n']} 条消息、认识 {users['n']} 人、{groups['n'] or 0} 个群、"
            f"{facts['n']} 条事实、{terms['n']} 个黑话词、{summaries['n']} 段压缩记忆；"
            f"本次已运行 {uptime_min} 分钟"
        )

    # ---------- 自检 ----------

    async def _tool_qq_group_history(self, args: dict, ctx: dict) -> str:
        """拉取群聊历史消息，给模型补上下文"""
        gid = args.get("group_id") or ctx.get("group_id")
        if not gid:
            return "错误：需要群号"
        count = min(int(args.get("count") or 15), 40)
        resp = await self._api("get_group_msg_history", {
            "group_id": int(gid),
            "count": count,
        }, timeout=12)
        if not resp or resp.get("status") == "failed":
            return "拉取群历史失败（接口可能不支持）"
        msgs = (resp.get("data") or {}).get("messages") or []
        if not msgs:
            return "最近没有群消息记录"

        lines = []
        for m in msgs[-count:]:
            sender = (m.get("sender") or {}).get("card") or \
                     (m.get("sender") or {}).get("nickname") or str(m.get("user_id"))
            text = m.get("raw_message") or ""
            if not text:
                # 数组格式消息，拼出可读文本
                parts = []
                for seg in (m.get("message") or []):
                    if isinstance(seg, dict):
                        if seg.get("type") == "text":
                            parts.append(seg.get("data", {}).get("text", ""))
                        elif seg.get("type") == "image":
                            parts.append("[图片]")
                        elif seg.get("type") == "face":
                            parts.append("[表情]")
                text = "".join(parts)
            text = (text or "").replace("\n", " ")[:100]
            if text:
                lines.append(f"{sender}: {text}")
        return "群 " + str(gid) + " 最近消息：\n" + "\n".join(lines[-count:])

    async def _tool_bot_selfcheck(self, args: dict, ctx: dict) -> str:
        checks = []

        # 1. 连接状态
        ws_ok = self.client.ws is not None
        checks.append(f"QQ连接:{'正常' if ws_ok else '断开'}（{self.client.self_id or '未就绪'}）")

        # 2. 运行时长
        uptime_min = int((time.time() - self._started_at) / 60)
        checks.append(f"运行:{uptime_min}分钟")

        # 3. 数据库
        try:
            size_mb = 0.0
            import os
            db_path = getattr(self.memory.db, "db_path", "")
            if db_path and os.path.exists(db_path):
                size_mb = os.path.getsize(db_path) / 1024 / 1024
            msgs = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM messages")
            checks.append(f"数据库:{size_mb:.2f}MB/{msgs['n']}条")
        except Exception as e:
            checks.append(f"数据库:异常({e})")

        # 4. 记忆量
        try:
            facts = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM user_facts")
            terms = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM learned_terms")
            checks.append(f"记忆:{facts['n']}事实/{terms['n']}黑话")
        except Exception:
            checks.append("记忆:读取失败")

        # 5. 心情
        if self.mood:
            checks.append(f"心情:{self.mood.current}({self.mood.energy:.0f})")

        # 6. 人格
        if self.persona_manager and self.persona_manager.current:
            checks.append(f"人格:{self.persona_manager.current.name}")

        # 7. 能力开关
        caps = []
        if self.vision:
            caps.append("视觉")
        if self.tool_executor_ready:
            caps.append("工具")
        checks.append("能力:" + ("+".join(caps) if caps else "无"))

        # 8. LLM 延迟（可选）
        if args.get("include_llm_ping") and self.llm:
            t0 = time.time()
            try:
                ok = await self.llm.chat([{"role": "user", "content": "回复一个「好」字"}],
                                         temperature=0, max_tokens=8)
                dt = (time.time() - t0) * 1000
                checks.append(f"LLM:{int(dt)}ms{'正常' if ok else '无响应'}")
            except Exception as e:
                checks.append(f"LLM:异常({e})")

        # 9. 最近报错（读日志文件尾部）
        try:
            import os
            log_path = "/app/logs/qqchat.log"
            if os.path.exists(log_path):
                with open(log_path, "rb") as f:
                    f.seek(max(0, os.path.getsize(log_path) - 20000))
                    tail = f.read().decode("utf-8", "ignore")
                errs = [l for l in tail.splitlines() if "[ERROR]" in l]
                checks.append(f"近期错误:{len(errs)}条")
                if errs:
                    checks.append("最近一条:" + errs[-1][:80])
        except Exception:
            pass

        # 10. 限流/预算状态
        if getattr(self, "rate_limiter", None):
            st = self.rate_limiter.stats()
            checks.append(
                f"今日额度剩余:{st['budget_left_pct']}%（已用 {st['day_chars']} 字符）"
            )

        return "自检结果：" + " | ".join(checks)

    # ---------- 学习 ----------

    async def _tool_bot_learn(self, args: dict, ctx: dict) -> str:
        if not self.learning:
            return "学习引擎未启用"
        target = args.get("target", "all")
        done = []
        try:
            if target in ("facts", "all"):
                await self.learning.learn_user_facts()
                done.append("用户事实")
            if target in ("terms", "all"):
                await self.learning.learn_group_terms()
                done.append("群黑话")
        except Exception as e:
            return f"学习失败: {e}"
        facts = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM user_facts")
        terms = await self.memory.db.fetchone("SELECT COUNT(*) AS n FROM learned_terms")
        return (f"已学习({'、'.join(done)})。"
                f"累计 {facts['n']} 条事实、{terms['n']} 个黑话词")

    async def _tool_server_time(self, args: dict, ctx: dict) -> str:
        return time.strftime("现在是 %Y年%m月%d日 %H:%M:%S")


# ---------- LLM 请求中的工具循环 ----------

async def chat_with_tools(llm, messages: list[dict], executor: ToolExecutor,
                          context: dict, max_rounds: int = 3,
                          temperature: float = 0.9, max_tokens: int = 2048,
                          tools: list | None = None) -> Optional[str]:
    """带工具调用的 LLM 对话循环

    tools: 可选，指定可用工具列表（默认全部 TOOLS）。
           网页端会传过滤后的列表，屏蔽需要 QQ 的工具。
    返回最终文本回复。
    """
    tools_payload = (tools if tools is not None else TOOLS) if executor else None

    for round_i in range(max_rounds):
        result = await llm.chat_raw(messages, tools=tools_payload,
                                    temperature=temperature, max_tokens=max_tokens)
        if result is None:
            return None

        # 纯文本回复，直接返回
        if result.get("type") == "text":
            return result["content"]

        # 工具调用
        if result.get("type") == "tool_calls":
            tool_calls = result["tool_calls"]
            # 把助手的工具调用消息加入上下文
            messages.append({
                "role": "assistant",
                "content": result.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    } for tc in tool_calls
                ],
            })
            # 执行每个工具
            for tc in tool_calls:
                outcome = await executor.execute(tc["name"], tc["arguments"], context)
                logger.info(f"🔧 工具 {tc['name']}({tc['arguments']}) → {str(outcome)[:60]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(outcome),
                })
            continue  # 带着工具结果再问 LLM

    # 超过轮次限制
    logger.warning("工具调用超过轮次限制")
    return None

