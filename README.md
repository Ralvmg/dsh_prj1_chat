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

## 部署

服务器已配置 git 自动部署：

```bash
bash deploy.sh
```

流程：`git fetch` → `reset --hard` → `py_compile` 语法检查（失败自动回滚）→ 重启容器 → 健康检查。

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
deploy.sh            服务器一键部署
```

## 致谢 / Credits

本项目整合了以下开源项目的成果，在此致谢：

### 角色素材与桌宠

- **[dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop)** by [MerZlin](https://github.com/MerZlin)
  - 提供「鲸鱼娘·大肥鱼」角色形象、106 段动画、24 张表情包
  - 本项目用 `_tools/build_assets.py` 把 WebM 动画转成 GIF 供 QQ / 网页使用
  - 桌宠端的事件桥接设计（`dsh-pet-bridge`）也启发了本项目的状态联动思路

### 协议与 QQ 接入

- **[NapCatQQ](https://github.com/NapNeko/NapCatQQ)** by NapNeko
  - 基于 NTQQ 的现代协议端，提供 OneBot v11 接口
- **[OneBot v11](https://github.com/botuniverse/onebot-11)** by Bot Universe
  - QQ 机器人统一协议标准

### 设计参考

- **[AstrBot](https://github.com/AstrBotDevs/AstrBot)** by AstrBotDevs（42k+ stars）
  - 提示词设计哲学：人设只写「你是谁」，不写「你该怎么说话」，把表达交给模型
  - 多平台 IM 适配的工程结构参考
- **[LangBot](https://github.com/langbot-app/LangBot)** by LangBot
  - Agent 型 IM 机器人的生产级架构参考

### 模型与语音

- **[DeepSeek-V4.1-Flash](https://deepseek.com)** — 对话与多模态理解（经 SiliconFlow API）
- **[edge-tts](https://github.com/rany2/edge-tts)** by rany2
  - 微软 Edge 免费语音合成，提供 6 种中文女声
- **[CosyVoice2](https://github.com/FunAudioLLM/CosyVoice)** by FunAudioLLM
  - 硅基流动托管的语音合成（付费兜底方案）

### 开发工具

- **[DeepSeek Harness](https://github.com/deepseek-ai)** — 本项目的开发与调试环境

## 作者

- **Ralvmg** — 项目发起与需求设计
  - GitHub: [@Ralvmg](https://github.com/Ralvmg)

- **DeepSeek Harness（AI Agent）** — 代码实现与架构设计
  - 本项目在 DeepSeek Harness 环境中由 AI Agent 协作完成
  - 涵盖：后端架构、记忆系统、工具系统、网页前端、语音接入、部署脚本

## License

MIT
