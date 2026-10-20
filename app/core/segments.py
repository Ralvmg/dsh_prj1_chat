"""消息段解析 —— 把 QQ 消息的每一段（文字/表情/图片/语音/at）翻译成人能读懂的描述

解决的核心问题：纯表情/纯图片消息以前会被当成"空消息"丢弃，机器人不回复。
现在任何消息都能被描述成文本，模型永远有东西可看。
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# QQ 原生表情 id → 名称（常用部分，够覆盖日常）
QQ_FACES = {
    0: "惊讶", 1: "撇嘴", 2: "色", 3: "发呆", 4: "得意", 5: "流泪", 6: "害羞",
    7: "闭嘴", 8: "睡", 9: "大哭", 10: "尴尬", 11: "发怒", 12: "调皮", 13: "呲牙",
    14: "微笑", 15: "难过", 16: "酷", 17: "冷汗", 18: "抓狂", 19: "吐",
    20: "偷笑", 21: "可爱", 22: "白眼", 23: "傲慢", 24: "饥饿", 25: "困",
    26: "惊恐", 27: "流汗", 28: "憨笑", 29: "悠闲", 30: "奋斗", 31: "咒骂",
    32: "疑问", 33: "嘘", 34: "晕", 35: "折磨", 36: "衰", 37: "骷髅",
    38: "敲打", 39: "再见", 40: "擦汗", 41: "抠鼻", 42: "鼓掌", 43: "糗大了",
    44: "坏笑", 45: "左哼哼", 46: "右哼哼", 47: "哈欠", 48: "鄙视", 49: "委屈",
    50: "快哭了", 51: "阴险", 52: "亲亲", 53: "吓", 54: "可怜", 55: "菜刀",
    56: "西瓜", 57: "啤酒", 58: "篮球", 59: "乒乓", 60: "咖啡", 61: "饭",
    62: "猪头", 63: "玫瑰", 64: "凋谢", 65: "示爱", 66: "爱心", 67: "心碎",
    68: "蛋糕", 69: "闪电", 70: "炸弹", 71: "刀", 72: "足球", 73: "瓢虫",
    74: "便便", 75: "月亮", 76: "太阳", 77: "礼物", 78: "拥抱", 79: "强",
    80: "弱", 81: "握手", 82: "胜利", 83: "抱拳", 84: "勾引", 85: "拳头",
    86: "差劲", 87: "爱你", 88: "NO", 89: "OK", 90: "爱情", 91: "飞吻",
    92: "跳跳", 93: "发抖", 94: "怄火", 95: "转圈", 96: "磕头", 97: "回头",
    98: "跳绳", 99: "挥手", 100: "激动", 101: "街舞", 102: "献吻", 103: "左太极",
    104: "右太极", 105: "开心", 106: "双喜", 107: "鞭炮", 108: "灯笼",
    109: "K歌", 110: "购物", 111: "邮件", 112: "帅", 113: "喝彩", 114: "祈祷",
    115: "爆筋", 116: "棒棒糖", 117: "喝奶", 118: "下面", 119: "香蕉",
    120: "飞机", 121: "开车", 122: "高铁", 123: "左车头", 124: "车厢",
    125: "右车头", 126: "多云", 127: "下雨", 128: "钞票", 129: "熊猫",
    130: "灯泡", 131: "风车", 132: "闹钟", 133: "打伞", 134: "彩球",
    135: "钻戒", 136: "沙发", 137: "纸巾",
    172: "咦", 173: "眨眼睛", 174: "泪奔", 175: "无奈", 176: "卖萌", 177: "小纠结",
    178: "斜眼笑", 179: "doge", 180: "惊喜", 181: "骚扰", 182: "笑哭",
    183: "我最美", 184: "河蟹", 185: "羊驼", 186: "栗子", 187: "幽灵",
    188: "蛋", 189: "马赛克", 190: "菊花", 191: "外星", 192: "火车",
}

# 表情名称 → 情绪倾向（帮模型判断对方语气）
FACE_EMOTION_HINTS = {
    "微笑": "礼貌或平静", "可爱": "卖萌/亲昵", "呲牙": "开心调侃", "流泪": "难过",
    "大哭": "很难过", "发怒": "生气", "白眼": "无语/嫌弃", "偷笑": "憋笑",
    "坏笑": "不怀好意", "斜眼笑": "调侃/得意", "狗头": "反讽/玩笑", "doge": "反讽/玩笑",
    "笑哭": "觉得好笑", "疑问": "困惑", "可怜": "求安慰", "害羞": "不好意思",
    "睡": "困", "困": "困", "鼓掌": "称赞", "强": "夸赞", "弱": "吐槽",
    "爱心": "喜欢", "亲亲": "亲密", "拥抱": "亲昵", "委屈": "不满/撒娇",
    "难过": "情绪低落", "惊恐": "被吓到", "憨笑": "犯傻/开心",
}


def describe_face(face_id: int) -> str:
    """把表情 id 描述成文字（给模型看，不是给用户看）

    未知 id 一律用通用描述，绝不回显 "#id" 这种占位符 ——
    否则模型会照着占位符格式编造输出，用户就会看到 "[QQ表情#427]" 这种鬼东西。
    """
    name = QQ_FACES.get(face_id)
    if not name:
        # 新版大表情/超级表情（QQ 客户端渲染，拿不到图片数据）
        # 给一个中性描述，模型当"对方做了个表情"处理即可
        return "[表情]"
    hint = FACE_EMOTION_HINTS.get(name)
    return f"[表情:{name}（{hint}）]" if hint else f"[表情:{name}]"


# ---------- 输出归一化：把模型写的"描述"转成真正的 CQ 码 ----------

# 名称 → id 反查表（忽略重复名，取最小 id）
FACE_NAME_TO_ID = {}
for _fid, _fname in sorted(QQ_FACES.items()):
    FACE_NAME_TO_ID.setdefault(_fname, _fid)

# 中英文别名
FACE_ALIASES = {
    "微笑": 14, "可爱": 21, "呲牙": 13, "大笑": 13, "哭": 9, "大哭": 9,
    "憨笑": 28, "斜眼笑": 178, "狗头": 179, "doge": 179, "笑哭": 182,
    "可怜": 111, "抱抱": 78, "拥抱": 78, "亲亲": 52, "害羞": 6,
    "睡": 8, "困": 25, "白眼": 22, "无语": 22, "疑惑": 32, "疑问": 32,
    "愤怒": 11, "生气": 11, "发怒": 11, "鼓掌": 42, "强": 79, "赞": 79,
    "弱": 80, "爱心": 66, "心碎": 67, "玫瑰": 63, "礼物": 77,
    "惊讶": 0, "惊恐": 26, "流汗": 27, "冷汗": 17, "偷笑": 20,
    "调皮": 12, "得意": 4, "发呆": 3, "酷": 16, "坏笑": 44,
}


def normalize_reply(text: str) -> str:
    """把模型输出里的"表情描述"归一化成真 CQ 码，并清掉不该出现的占位符

    处理这几种模型常见的错误输出：
    - [QQ表情#427]        → [CQ:face,id=427]（兜底，未知 id 也照发）
    - [表情:可爱]         → [CQ:face,id=21]
    - [表情:可爱（卖萌）] → [CQ:face,id=21]
    - [表情:表情包/大表情] → 删除（没有 id 可发）
    - [表情包:xxx]        → 删除
    - [图片]              → 删除（没 URL 发不了）
    """
    if not text:
        return text

    # 0. 剥离服务端安全分类 / 工具标记等元数据（模型偶尔会把内部标记混进正文）
    text = re.sub(r"<ds_safety>[\s\S]*?</ds_safety>", "", text, flags=re.I)
    text = re.sub(r"<ds_safety>[\s\S]*$", "", text, flags=re.I)
    text = re.sub(r"<ds_tool>[\s\S]*?</ds_tool>", "", text, flags=re.I)
    text = re.sub(r"<ds_tool>[\s\S]*$", "", text, flags=re.I)
    # 零散的 <tool_name>xx</tool_name> / <parameters>{...}</parameters>
    text = re.sub(r"<tool_name>[\s\S]*?</tool_name>", "", text, flags=re.I)
    text = re.sub(r"<parameters>[\s\S]*?</parameters>", "", text, flags=re.I)
    text = re.sub(r"</?ds_[a-z_]+[^>]*>", "", text, flags=re.I)
    # 开头的 [mpp] 之类垃圾前缀
    text = re.sub(r"^\s*\[mpp\]\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*(Safe|Unsafe|safe|unsafe)\s*$", "", text, flags=re.M)
    text = text.strip()
    if not text:
        return ""

    # 1. [QQ表情#427] / [QQ表情#427] 之类 → 真 CQ 码
    def _qq_face(m):
        return f"[CQ:face,id={m.group(1)}]"
    text = re.sub(r"\[QQ\s*表情\s*#?\s*(\d+)\]", _qq_face, text)
    text = re.sub(r"\[表情\s*#\s*(\d+)\]", _qq_face, text)

    # 2. [表情:名称] / [表情:名称（提示）] → 查表转 CQ 码
    def _named_face(m):
        raw = m.group(1)
        # 去掉括号里的情绪提示
        name = re.sub(r"[（(].*?[)）]", "", raw).strip()
        fid = FACE_ALIASES.get(name) or FACE_NAME_TO_ID.get(name)
        if fid is not None:
            return f"[CQ:face,id={fid}]"
        return ""  # 查不到就删掉，绝不留下描述文字

    text = re.sub(r"\[表情:([^\]]+)\]", _named_face, text)

    # 3. 无法发送的占位符直接清掉（表情包描述可能嵌套方括号，逐个吃掉）
    text = re.sub(r"\[表情包:(?:[^\[\]]|\[[^\]]*\])*\]", "", text)
    text = re.sub(r"\[图片\]", "", text)
    text = re.sub(r"\[语音\]", "", text)
    text = re.sub(r"\[视频\]", "", text)
    text = re.sub(r"\[卡片/转发消息\]", "", text)

    # 4. 清理多余空行/空格
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _seg_data(seg: dict) -> dict:
    return seg.get("data", {}) or {}


def describe_event(event: dict, bot_name: str = "") -> tuple[str, dict]:
    """把整条消息描述成文本 + 附带元信息

    返回 (描述文本, meta)
    meta 里包含图片列表、是否有媒体内容等，供上层决定是否走视觉模型。
    """
    msg = event.get("message", "")
    self_id = str(event.get("self_id", ""))
    parts: list[str] = []
    images: list[dict] = []
    replied_ids: list[str] = []
    has_media = False

    # 字符串格式（CQ 码）
    if isinstance(msg, str):
        import re
        # 逐段替换 CQ 码
        def repl(m):
            nonlocal has_media
            cq = m.group(1)
            kv = {}
            for piece in cq.split(","):
                if "=" in piece:
                    k, _, v = piece.partition("=")
                    kv[k.strip()] = v.strip()
            typ = cq.split(",")[0]
            if typ == "face":
                return describe_face(int(kv.get("id", 0)))
            if typ == "image":
                has_media = True
                url = kv.get("url") or kv.get("file") or ""
                images.append({"url": url, "file": kv.get("file", "")})
                return "[图片]"
            if typ in ("mface", "bface", "sface"):
                has_media = True
                s = kv.get("summary") or "表情包"
                return f"[表情包:{s}]"
            if typ == "record":
                has_media = True
                return "[语音]"
            if typ == "video":
                has_media = True
                return "[视频]"
            if typ == "at":
                qq = kv.get("qq", "")
                nm = kv.get("name", "")
                if qq == self_id:
                    return "@你 "
                if qq == "all":
                    return "@全体成员 "
                return f"@{nm}(QQ:{qq}) " if nm else f"@QQ:{qq} "
            if typ == "reply":
                # 引用消息：把被引用者的 QQ / 昵称 / 原文都带上，供模型理解"在回复谁、回什么"
                rqq = kv.get("qq", "")
                rname = kv.get("name", "") or rqq
                rtext = kv.get("text", "") or kv.get("content", "")
                tag = "（引用你）" if rqq == self_id else f"（引用 {rname}）"
                body = f"「{rtext}」" if rtext else ""
                return f"[{tag}{body}] "
            return ""

        text = re.sub(r"\[CQ:([^\]]+)\]", repl, msg).strip()
        # 纯 @ 而没有正文，也算有内容
        if not text and ("[CQ:at" in msg or "[CQ:face" in msg):
            text = "（发了个表情/at 了你）"
        return text, {"images": images, "has_media": has_media, "is_empty": not text}

    # 数组格式（NapCat messagePostFormat=array）
    for seg in msg:
        if not isinstance(seg, dict):
            continue
        typ = seg.get("type")
        d = _seg_data(seg)

        if typ == "text":
            t = d.get("text", "")
            if t:
                parts.append(t)
        elif typ == "face":
            parts.append(describe_face(int(d.get("id", 0))))
        elif typ == "image":
            has_media = True
            url = d.get("url") or ""
            file = d.get("file") or ""
            images.append({"url": url, "file": file, "sub_type": d.get("sub_type")})
            parts.append("[图片]")
        elif typ in ("mface", "bface", "sface", "dice"):
            has_media = True
            summary = d.get("summary") or d.get("text") or "表情包"
            # 表情包也可能带图（热图/动图），有 url 就交给多模态模型看
            murl = d.get("url") or ""
            mfile = d.get("file") or d.get("path") or ""
            if murl.startswith("http") or mfile:
                images.append({"url": murl, "file": mfile, "kind": typ})
            parts.append(f"[表情包:{summary}]")
        elif typ == "record":
            has_media = True
            parts.append("[语音]")
        elif typ == "video":
            has_media = True
            parts.append("[视频]")
        elif typ == "at":
            qq = str(d.get("qq", ""))
            name = d.get("name") or ""
            if qq == self_id:
                parts.append("@你 ")
            elif qq and qq != "all":
                # 带上 QQ 号，模型才能知道"在跟谁说话"
                parts.append(f"@{name}(QQ:{qq}) " if name else f"@QQ:{qq} ")
            elif qq == "all":
                parts.append("@全体成员 ")
        elif typ == "reply":
            # 数组格式的 reply 段：NapCat 可能给 id / qq / name / text
            rqq = str(d.get("qq", "") or "")
            rname = d.get("name") or rqq
            rtext = d.get("text") or d.get("content") or ""
            rid = str(d.get("id", "") or "")
            if rid:
                replied_ids.append(rid)
            tag = "引用你" if rqq == self_id else (f"引用 {rname}" if rname else "引用消息")
            body = f"「{rtext}」" if rtext else ""
            parts.append(f"[{tag}{body}] ")
        elif typ in ("forward", "json", "xml"):
            has_media = True
            parts.append("[卡片/转发消息]")
        elif typ == "file":
            has_media = True
            parts.append("[文件]")

    text = "".join(parts).strip()
    return text, {"images": images, "has_media": has_media,
                  "is_empty": not text, "replied_ids": replied_ids}


