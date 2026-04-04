# Ultrabot 循序渐进教程

> 从零开始构建一个生产级 AI 助手框架

本指南将带你从"向 LLM 问好"一步步走到一个完整的多提供者、多通道 AI 智能体，具备工具调用、记忆、安全防护和 Web 界面。每节课程都建立在上一节课的基础之上，每节课都包含可运行的代码和测试。

## 致谢

本教程的主要思路来自于 [Nanobot](https://github.com/HKUDS/nanobot) 以及 [Learn-Claude-Code](https://github.com/shareAI-lab/learn-claude-code/)，所以对应的叫做 Ultrabot。  
本课程设计由 AI 辅助下完成，更新地址见 [GitHub](https://github.com/junfhu/UltrabotStepByStep)，如果您觉得对您有帮助，请帮助点亮一颗星。  
本课程中使用的大模型提供商是火山引擎 Code Plan，如果正好你也需要，可以使用我的邀请码获取 9 折优惠：[邀请链接](https://volcengine.com/L/_01BJCkKdMc/)（邀请码：HHCDB4J4）

## 前置条件

- Python 3.12+（推荐使用 pyenv 管理）
- OpenAI 兼容的 API 密钥

## 课程目录

### 第一部分：基础（课程 0-4）

| 课程 | 标题 | 简介 |
|------|------|------|
| 00 | [介绍](00-introduction.md) | 项目整体目标、前置条件和课程设计思路 |
| 01 | [向 LLM 问好 -- 你的第一次 AI 对话](01-hello-llm-your-first-ai-conversation.md) | 用 10 行 Python 代码与 LLM 对话，学习 chat completions API 和多轮对话 |
| 02 | [流式输出 + 智能体循环](02-streaming-the-agent-loop.md) | 实现实时流式输出，将聊天机器人重构为带运行循环的 Agent 类 |
| 03 | [工具调用 -- 赋予 LLM 超能力](03-tool-calling-give-the-llm-superpowers.md) | 让 LLM 调用函数与真实世界交互，建立 Tool 抽象基类和 ToolRegistry |
| 04 | [更多工具 + 工具集组合](04-more-tools-toolset-composition.md) | 添加更多工具并分组为可启用/禁用的命名工具集 |

### 第二部分：架构（课程 5-8）

| 课程 | 标题 | 简介 |
|------|------|------|
| 05 | [配置系统](05-configuration-system.md) | 使用 Pydantic BaseSettings 构建类型化配置，支持 JSON 文件和环境变量覆盖 |
| 06 | [提供者抽象 -- 多 LLM 支持](06-provider-abstraction-multiple-llms.md) | 将 LLM 通信抽取为可插拔的提供者系统，支持 OpenAI、DeepSeek、Groq 等任意后端 |
| 07 | [Anthropic 提供者 -- 添加 Claude](07-anthropic-provider-adding-claude.md) | 添加原生 Anthropic（Claude）支持，处理不同 LLM API 之间的格式转换 |
| 08 | [CLI + 交互式 REPL](08-cli-interactive-repl.md) | 使用 Typer、Rich Live 和 prompt_toolkit 构建完善的命令行界面 |

### 第三部分：可靠性与事件（课程 9-12）

| 课程 | 标题 | 简介 |
|------|------|------|
| 09 | [会话持久化 -- 记住对话](09-session-persistence-remembering-conversations.md) | 会话持久化，记住对话 |
| 10 | [熔断器 + 提供者故障转移](10-circuit-breaker-provider-failover.md) | 熔断器和提供者故障转移机制 |
| 11 | [消息总线 + 事件](11-message-bus-events.md) | 消息总线和事件驱动架构 |
| 12 | [安全守卫](12-security-guard.md) | 安全防护和访问控制 |

### 第四部分：多通道（课程 13-16）

| 课程 | 标题 | 简介 |
|------|------|------|
| 13 | [通道基类 + Telegram](13-channel-base-telegram.md) | 通道基类和 Telegram 集成 |
| 14 | [Discord + Slack 通道](14-discord-slack-channels.md) | Discord 和 Slack 通道支持 |
| 15 | [网关服务器 -- 多通道编排](15-gateway-server-multi-channel-orchestration.md) | 网关服务器多通道编排 |
| 16 | [中国平台通道（企业微信、微信、飞书、QQ）](16-chinese-platform-channels-wecom-weixin-feishu-qq.md) | 中国平台通道集成 |

### 第五部分：专家系统（课程 17-18）

| 课程 | 标题 | 简介 |
|------|------|------|
| 17 | [专家系统 -- 人设](17-expert-system-personas.md) | 专家系统和人设管理 |
| 18 | [专家路由器 + 动态切换](18-expert-router-dynamic-switching.md) | 专家路由器和动态切换 |

### 第六部分：Web 与自动化（课程 19-21）

| 课程 | 标题 | 简介 |
|------|------|------|
| 19 | [Web 界面 -- 基于浏览器的聊天](19-web-ui-browser-based-chat.md) | 构建 FastAPI 后端，包含 REST 端点和 WebSocket 流式传输，提供基于浏览器的聊天界面 |
| 20 | [定时任务调度器 -- 自动化任务](20-cron-scheduler-automated-tasks.md) | 构建基于时间的任务调度器，按 cron 表达式通过消息总线触发消息 |
| 21 | [守护进程管理器 + 心跳](21-daemon-manager-heartbeat.md) | 将 ultrabot 作为系统守护进程运行，并对所有 LLM 提供者进行定期健康检查心跳 |

### 第七部分：智能增强（课程 22-26）

| 课程 | 标题 | 简介 |
|------|------|------|
| 22 | [记忆存储 -- 长期知识](22-memory-store-long-term-knowledge.md) | 构建持久化记忆存储，使用 SQLite FTS5 全文搜索和时间衰减评分，加上智能上下文组装引擎 |
| 23 | [媒体管道 -- 图片和文档](23-media-pipeline-images-and-documents.md) | 构建媒体处理管道，用于获取、处理和存储图片及文档，并具备 SSRF 防护 |
| 24 | [智能分块 -- 平台感知的消息拆分](24-smart-chunking-platform-aware-message-splitting.md) | 将较长的机器人回复拆分为各平台安全的片段，不破坏代码块和句子完整性 |
| 25 | [上下文压缩 -- 扩展长对话](25-context-compression-scaling-long-conversations.md) | 当对话历史接近上下文窗口时自动压缩，将关键信息保留在结构化摘要中 |
| 26 | [提示词缓存 + 辅助客户端](26-prompt-caching-auxiliary-client.md) | 通过 Anthropic 提示词缓存降低约 75% API 成本，新增廉价"辅助" LLM 用于元数据任务 |

### 第八部分：安全与高级功能（课程 27-28）

| 课程 | 标题 | 简介 |
|------|------|------|
| 27 | [安全加固 -- 注入检测 + 凭证脱敏](27-security-hardening-injection-detection-credential-.md) | 防御提示词注入攻击，并防止凭证在日志和聊天输出中泄露 |
| 28 | [浏览器自动化 + 子智能体委派](28-browser-automation-subagent-delegation.md) | 为智能体提供无头浏览器进行网页交互的能力，以及将子任务委派给隔离子智能体的能力 |

### 第九部分：生产就绪（课程 29-30）

| 课程 | 标题 | 简介 |
|------|------|------|
| 29 | [运维完善 -- 用量追踪、更新、诊断、主题、密钥轮换](29-operational-polish-usage-updates-doctor-themes-aut.md) | 添加生产就绪的运维功能：用量追踪、自更新、配置诊断、主题、API 密钥轮换、群聊激活、设备配对、技能、MCP 和标题生成 |
| 30 | [完整项目打包 -- 交付上线！](30-full-project-packaging-ship-it.md) | 将课程 1-29 中构建的所有内容打包为规范的可安装 Python 项目，包含 pyproject.toml、入口点、CI 配置和完整 README |

## 许可证

本项目仅供学习使用。
