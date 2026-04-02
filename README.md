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

> 更多课程正在持续更新中，敬请期待...

## 许可证

本项目仅供学习使用。
