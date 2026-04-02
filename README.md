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

### 第二部分：配置与多提供者（课程 5-7）

| 课程 | 标题 | 简介 |
|------|------|------|
| 05 | [配置系统](05-configuration-system.md) | 使用 Pydantic、JSON 文件和环境变量构建配置系统 |
| 06 | [提供者抽象 -- 多 LLM 支持](06-provider-abstraction-multiple-llms.md) | 将 LLM 通信抽取为可插拔的提供者系统，支持重试逻辑 |
| 07 | [Anthropic 提供者 -- 添加 Claude](07-anthropic-provider-adding-claude.md) | 添加原生 Anthropic（Claude）支持，处理与 OpenAI API 的差异 |

### 第三部分：交互界面与持久化（课程 8-9）

| 课程 | 标题 | 简介 |
|------|------|------|
| 08 | [CLI + 交互式 REPL](08-cli-interactive-repl.md) | 使用 Typer、Rich 和 prompt_toolkit 构建命令行界面 |
| 09 | [会话持久化 -- 记住对话](09-session-persistence-remembering-conversations.md) | 将对话以 JSON 文件持久化，支持 token 估算和上下文窗口修剪 |

### 第四部分：可靠性与安全（课程 10-12）

| 课程 | 标题 | 简介 |
|------|------|------|
| 10 | [熔断器 + 提供者故障转移](10-circuit-breaker-provider-failover.md) | 通过熔断器模式和自动故障转移保护智能体免受级联故障 |
| 11 | [消息总线 + 事件](11-message-bus-events.md) | 基于优先级的异步消息总线，解耦消息生产者与消费者 |
| 12 | [安全守卫](12-security-guard.md) | 速率限制、输入清理、阻止危险模式和逐通道访问控制 |

### 第五部分：多通道接入（课程 13-16）

| 课程 | 标题 | 简介 |
|------|------|------|
| 13 | [通道基类 + Telegram](13-channel-base-telegram.md) | 定义通道抽象基类，实现 Telegram 通道 |
| 14 | [Discord + Slack 通道](14-discord-slack-channels.md) | 添加 Discord 和 Slack 作为消息通道 |
| 15 | [网关服务器 -- 多通道编排](15-gateway-server-multi-channel-orchestration.md) | 构建网关连接智能体、消息总线、安全守卫和所有通道 |
| 16 | [中国平台通道（企业微信、微信、飞书、QQ）](16-chinese-platform-channels-wecom-weixin-feishu-qq.md) | 接入四个主要中国消息平台 |

### 第六部分：智能路由与专家系统（课程 17-18）

| 课程 | 标题 | 简介 |
|------|------|------|
| 17 | [专家系统 -- 人设](17-expert-system-personas.md) | 基于人设的专家系统，解析 markdown 人设文件 |
| 18 | [专家路由器 + 动态切换](18-expert-router-dynamic-switching.md) | 智能消息路由，支持显式命令、粘性会话和 LLM 自动路由 |

### 第七部分：Web 界面与自动化（课程 19-21）

| 课程 | 标题 | 简介 |
|------|------|------|
| 19 | [Web 界面 -- 基于浏览器的聊天](19-web-ui-browser-based-chat.md) | FastAPI 后端 + WebSocket 流式传输的浏览器聊天界面 |
| 20 | [定时任务调度器 -- 自动化任务](20-cron-scheduler-automated-tasks.md) | 基于 cron 表达式的任务调度器 |
| 21 | [守护进程管理器 + 心跳](21-daemon-manager-heartbeat.md) | 系统守护进程运行和定期健康检查 |

### 第八部分：高级功能（课程 22-28）

| 课程 | 标题 | 简介 |
|------|------|------|
| 22 | [记忆存储 -- 长期知识](22-memory-store-long-term-knowledge.md) | SQLite FTS5 全文搜索 + 时间衰减评分的长期记忆 |
| 23 | [媒体管道 -- 图片和文档](23-media-pipeline-images-and-documents.md) | 媒体处理管道：SSRF 防护、图片缩放、PDF 提取 |
| 24 | [智能分块 -- 平台感知的消息拆分](24-smart-chunking-platform-aware-message-splitting.md) | 将长回复拆分为各平台安全的片段 |
| 25 | [上下文压缩 -- 扩展长对话](25-context-compression-scaling-long-conversations.md) | 自动压缩对话历史，基于 LLM 摘要保留关键信息 |
| 26 | [提示词缓存 + 辅助客户端](26-prompt-caching-auxiliary-client.md) | Anthropic 提示词缓存降低约 75% 成本，廉价辅助 LLM |
| 27 | [安全加固 -- 注入检测 + 凭证脱敏](27-security-hardening-injection-detection-credential-.md) | 防御提示词注入攻击，防止凭证泄露 |
| 28 | [浏览器自动化 + 子智能体委派](28-browser-automation-subagent-delegation.md) | 无头浏览器网页交互 + 子任务委派给隔离子智能体 |

### 第九部分：生产就绪（课程 29-30）

| 课程 | 标题 | 简介 |
|------|------|------|
| 29 | [运维完善 -- 用量追踪、诊断、主题、密钥轮换](29-operational-polish-usage-updates-doctor-themes-aut.md) | 用量追踪、自更新、配置诊断、主题和 API 密钥轮换 |
| 30 | [完整项目打包 -- 交付上线！](30-full-project-packaging-ship-it.md) | 打包为可安装 Python 项目，含 pyproject.toml、CI 和完整文档 |

## 架构总览

```
用户消息
  │
  ▼
┌─────────────────────────────────────────┐
│  通道层（Telegram / Discord / Slack /   │
│         微信 / 飞书 / QQ / Web）        │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│            消息总线 + 安全守卫            │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│         专家路由器 → 专家人设             │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│  智能体（Agent）                         │
│  ├── 工具注册表 + 工具集                  │
│  ├── 会话管理 + 记忆存储                  │
│  └── 上下文压缩 + 智能分块               │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│  提供者层（熔断器 + 故障转移）            │
│  ├── OpenAI 兼容提供者                    │
│  └── Anthropic（Claude）提供者            │
└──────────────────────────────────────────┘
```

## 许可证

本项目仅供学习使用。
