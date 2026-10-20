# QQChat 项目结构说明

## 目录分层

```
qqchat/
├── docker-compose.yml          # 编排入口
├── .env                        # 环境变量（密钥，不进版本控制）
├── .env.example                # 配置模板
│
├── app/                        # ⭐ 机器人应用（核心代码）
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # 程序入口：WebSocket 连接、事件循环
│   ├── config.py               # 配置集中管理
│   ├── core/                   # 核心逻辑
│   │   ├── __init__.py
│   │   ├── onebot.py           # OneBot v11 协议客户端
│   │   ├── handler.py          # 消息分发与决策
│   │   └── reply.py            # 回复策略（分段、延迟、拟人化）
│   ├── memory/                 # 记忆系统
│   │   ├── __init__.py
│   │   ├── database.py         # SQLite 封装
│   │   ├── conversation.py     # 对话历史
│   │   └── profile.py          # 用户画像（长期记忆）
│   ├── llm/                    # 模型接入
│   │   ├── __init__.py
│   │   ├── client.py           # OpenAI 兼容客户端
│   │   └── prompt.py           # 提示词构建
│   └── persona/                # 人设系统
│       ├── __init__.py
│       ├── loader.py           # 人设加载器
│       └── builtin/            # 内置人设（YAML）
│           ├── whale-musume.yml    # 鲸鱼娘·大肥鱼
│           ├── gentle.yml          # 温柔版
│           └── tsundere.yml        # 毒舌版
│
├── napcat/                     # QQ 协议端配置（NapCatQQ）
│   └── config/
│
├── data/                       # 运行时数据（挂载卷）
│   ├── qqchat.db               # 记忆数据库
│   └── personas/               # 用户自定义人设
│
├── logs/                       # 日志（挂载卷）
│   ├── app/
│   └── napcat/
│
└── scripts/                    # 运维脚本
    ├── deploy.sh               # 部署
    ├── backup.sh               # 备份记忆
    └── health.sh               # 健康检查
```

## 分层原则

1. **app/ 只放代码**，配置通过环境变量注入
2. **data/ 和 logs/ 是挂载卷**，容器重建不丢数据
3. **persona/builtin/** 内置人设随代码走，`data/personas/` 放用户自定义
4. **core/ 管流程，memory/ 管状态，llm/ 管外部调用**，职责单一
5. **scripts/ 放运维命令**，不混在应用代码里

## 数据流

```
QQ 消息
  ↓ NapCatQQ (协议端)
  ↓ OneBot v11 WebSocket
core/onebot.py (接收)
  ↓
core/handler.py (决定是否回复)
  ↓
llm/prompt.py (组装上下文: 人设 + 历史 + 画像)
  ↓
llm/client.py (调用 API)
  ↓
core/reply.py (拟人化处理: 分段/延迟)
  ↓
core/onebot.py (发送)
  ↓
QQ 消息
```

## 记忆分层

| 层级 | 存储 | 生命周期 | 用途 |
|---|---|---|---|
| 短期 | 内存 | 单次对话 | 当前上下文窗口 |
| 中期 | `messages` 表 | 30 天 | 对话历史 |
| 长期 | `user_profile` 表 | 永久 | 用户画像、偏好 |
| 群设置 | `group_settings` 表 | 永久 | 群专属人设 |
