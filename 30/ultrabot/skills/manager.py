# ultrabot/skills/manager.py
# SkillManager 从磁盘发现技能（SKILL.md + 可选 tools/）。
# 支持通过 reload() 方法热重载。

# ultrabot/mcp/client.py
# MCPClient 通过 stdio 或 HTTP 传输连接 MCP 服务器。
# 将每个服务器工具封装为本地 MCPToolWrapper(Tool)。

# ultrabot/agent/title_generator.py
# generate_title() 使用辅助客户端为对话创建 3-7 个词的标题。
# 失败时回退到第一条用户消息的前 50 个字符。
