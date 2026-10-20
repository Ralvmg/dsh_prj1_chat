# 鲸鱼娘 Chat（DSH Project 1）

一个会聊天、有记忆、能看图的 AI 角色机器人。支持 **QQ 机器人** 和 **网页聊天** 两个通道，共用同一套后端。

## 功能

- **拟人化对话**：短句多条发送、拟人化延迟、可打断合并
- **长期记忆**：对话历史、用户画像、结构化事实、群内黑话
- **人设系统**：3 种性格（大肥鱼 / 软软 / 辣辣），心情自动切换
- **多模态**：能看懂图片和表情包
- **30+ 工具**：联网搜索、热点、查群/查人、发表情包、知识库等
- **语音朗读**：免费 edge-tts（6 种中文女声），带节流和缓存
- **网页聊天**：深色 UI、人设切换、语音播放、会话持久化

## 架构

```
QQ 通道                    网页通道
   ↓                          ↓
NapCat (OneBot v11)      nginx → HTTP API
   ↓                          ↓
   └──────── qqchat-app ──────┘
              ├── 记忆（SQLite）
              ├── 人设引擎
              ├── 工具系统
              ├── 心情引擎
              └── TTS（edge-tts）
```

## 快速开始

```bash
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY
docker compose up -d
```

## 目录结构

```
app/
  main.py            入口，调度消息
  config.py          配置
  core/
    handler.py       消息处理主流程
    onebot.py        OneBot 客户端
    segments.py      消息段解析（含引用、表情）
    reply.py         拟人化回复策略
    webserver.py     网页聊天 HTTP API
    media_assets.py  动画/表情包管理
    opssession.py    服务器操作会话
  llm/
    client.py        LLM 客户端
    prompt.py        提示词构建
    tools.py         工具定义与执行
    tts.py           语音合成
    vision.py        图片理解
    web.py           联网搜索
  memory/            记忆系统
  persona/           人设文件
web/
  index.html         网页聊天界面
```

## 素材说明

鲸鱼娘动画和表情包来自 [dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop)，用 `_tools/build_assets.py` 从 WebM 转成 GIF。因体积较大（23MB）不入库，需要时自行生成：

```bash
git clone https://github.com/MerZlin/dsh-pet-indesktop /tmp/pet-src
python3 _tools/build_assets.py
```

## License

MIT
