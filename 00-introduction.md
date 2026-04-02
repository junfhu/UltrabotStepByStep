# Ultrabot：30 课程开发指南
**从零开始构建一个生产级 AI 助手框架。**
本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上。每节课都包含可运行的代码和测试。  
本教程的主要思路来自于Nanobot(https://github.com/HKUDS/nanobot)以及Learn-Claude-Code(https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做Ultrabot。  
本课程设计由AI辅助下完成，更新地址见https://github.com/junfhu/UltrabotStepByStep，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎Code Plan，如果正好你也需要，可以使用我的邀请码获取9折优惠 https://volcengine.com/L/_01BJCkKdMc/  邀请码：HHCDB4J4）  



## 前置条件

- **pyenv**（Python 版本管理器）—— 本指南全程使用
- **Python 3.12**（通过 pyenv 安装）
- **一个 OpenAI 兼容的 API 密钥**（任何OPENAI兼容提供者）
- **一个文本编辑器**（任何文本编辑器什么都行）

就这些。不需要构建工具，不需要包管理器，不需要框架。只有当你需要时，我们才会增加复杂度。

### 为什么用 pyenv？

系统自带的 Python 不可靠 —— 不同操作系统版本自带不同的 Python 版本，
修改它可能会破坏系统工具。`pyenv` 让你完全掌控：安装任意 Python 版本、
在它们之间切换、创建隔离环境。每个专业的 Python 项目都应该使用它。

快速安装（如果你还没有的话）：

```bash
# Linux
curl https://pyenv.run | bash
```

然后添加到你的 shell 配置文件（`~/.bashrc`、`~/.zshrc` 等）：

```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
```

重启 shell，然后：

```bash
pyenv install 3.12
pyenv global 3.12
python --version  # Python 3.12.x
```

针对不同系统，有可能需要安装一些额外的包，需要自动问其他AI，我的Ubuntu22需要额外安装如下包
```bash
sudo apt update
sudo apt install -y make build-essential libssl-dev zlib1g-dev \
libbz2-dev libreadline-dev libsqlite3-dev curl \
libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev \
liblzma-dev git
# 安装更多编译依赖
sudo apt install -y pkg-config
sudo apt install -y libgdbm-dev libnss3-dev
sudo apt install -y libssl-dev libffi-dev
sudo apt install -y libbz2-dev libreadline-dev libsqlite3-dev
sudo apt install -y libncurses5-dev libncursesw5-dev liblzma-dev
```

## 如何使用本指南

1. **按顺序完成课程。** 每节课都建立在前一节课的基础上。
2. **自己动手敲代码** —— 不要只是复制粘贴。这样你会学到更多。
3. **每节课后运行测试。** 测试全绿 = 你做对了。
4. **阅读说明文字。** 代码注释解释的是 *为什么*，而不仅仅是 *是什么*。
5. **检查每个检查点。** 如果它不能工作，先调试再继续。

## 设计理念

我们从课程 1 就开始和 LLM 对话，然后一步步的丰富它，课程最后，打包成一个完整项目。

课程进度：
- **课程 1-4：** 和 LLM 对话、流式输出响应、调用工具 —— 有趣的部分
- **课程 5-8：** 添加完善的配置、多提供者支持和一个好用的 CLI
- **课程 9-16：** 生产基础设施 —— 持久化、弹性容错、通道、网关
- **课程 17-23：** 专家人设、Web 界面、定时任务、记忆、媒体处理
- **课程 24-29：** 高级 AI 功能和安全加固
- **课程 30：** 将所有内容打包成一个正式的 Python 项目并发布

## 架构概览

到课程 30 结束时，你将构建出：

```
ultrabot/
├── agent/          # AI 对话循环、提示词、压缩、委托
├── bus/            # 异步消息总线，支持优先级队列
├── channels/       # 7 个平台通道（Telegram、Discord、Slack、企业微信、微信、飞书、QQ）
├── chunking/       # 针对平台限制的智能消息分割
├── cli/            # 交互式 CLI，支持主题和流式输出
├── config/         # Pydantic 设置、迁移、诊断工具
├── cron/           # 任务调度器（APScheduler）
├── daemon/         # 后台进程管理器，支持 PID 文件
├── experts/        # 人设系统，使用 YAML 定义和自动路由
├── gateway/        # 多通道网关服务器（FastAPI）
├── heartbeat/      # 健康监控服务
├── mcp/            # Model Context Protocol 客户端
├── media/          # 图片/PDF 处理管线
├── memory/         # SQLite + FTS5 持久化记忆，支持重要性评分
├── providers/      # LLM 提供者（OpenAI、Anthropic），支持熔断器
├── security/       # 速率限制、注入检测、凭证脱敏
├── session/        # 对话持久化，支持 TTL + 裁剪
├── skills/         # 插件/技能系统
├── tools/          # 15 个内置工具 + 工具集组合
├── updater/        # 自更新，支持版本检查
├── usage/          # 按模型统计 token 和费用
└── webui/          # FastAPI + WebSocket 聊天界面
```

## 课程地图

| # | 课程 | 你将构建什么 |
|---|------|-------------|
| 1 | **向 LLM 问好** | 一个文件，`pip install openai`，和 GPT 对话 |
| 2 | **流式输出 + 智能体循环** | 流式 token，多轮对话 |
| 3 | **工具调用** | LLM 调用工具、执行、再回来 |
| 4 | **更多工具 + 工具集** | 15 个工具，命名分组，启用/禁用 |
| 5 | **配置系统** | Pydantic 设置，JSON 配置，环境变量 |
| 6 | **提供者抽象** | LLMProvider ABC，OpenAI 提供者，注册表 |
| 7 | **Anthropic 提供者** | Claude 支持，格式转换，流式输出 |
| 8 | **CLI + 交互式 REPL** | Typer、Rich、prompt_toolkit、斜杠命令 |
| 9 | **会话持久化** | JSON 存储、TTL、上下文窗口裁剪 |
| 10 | **熔断器 + 故障转移** | 状态机，自动提供者回退 |
| 11 | **消息总线** | 异步优先级队列、死信队列、扇出 |
| 12 | **安全守卫** | 速率限制、清洗、访问控制 |
| 13 | **Telegram 通道** | BaseChannel ABC，第一个消息平台 |
| 14 | **Discord + Slack** | 另外两个通道，支持平台格式化 |
| 15 | **网关服务器** | FastAPI，多通道编排 |
| 16 | **中国平台** | 企业微信、微信、飞书、QQ |
| 17 | **专家人设** | YAML 定义、解析器、注册表 |
| 18 | **专家路由** | 自动路由、热重载、/expert 命令 |
| 19 | **Web 界面** | FastAPI + WebSocket 浏览器聊天 |
| 20 | **定时调度** | APScheduler，持久化任务 |
| 21 | **守护进程 + 心跳** | 后台进程，健康监控 |
| 22 | **记忆存储** | SQLite + FTS5，重要性评分 |
| 23 | **媒体管线** | 图片、PDF、基于哈希的存储 |
| 24 | **智能分块** | 感知平台的消息分割 |
| 25 | **上下文压缩** | 基于 LLM 的摘要 |
| 26 | **提示缓存 + 辅助模型** | 缓存断点、廉价 LLM 客户端 |
| 27 | **注入检测 + 脱敏** | 安全加固 |
| 28 | **浏览器 + 委托** | Playwright 工具、子智能体生成 |
| 29 | **运维完善** | 用量统计、更新、诊断、主题、密钥轮换 |
| 30 | **项目打包** | pyproject.toml、入口点、CI、README |

---


接下来可以进入第一课
# UltraBot 开发者指南 —— 第 1 部分（课程 1-8）

> **从零到一个完善的多提供者 CLI 聊天机器人，支持工具调用。**
>
> 每节课程只增加一个核心概念。课程 1 对任何安装了 Python 的人来说
> 10 分钟即可完成。到课程 8 结束时，你将拥有一个
> 生产级的交互式助手。

---
