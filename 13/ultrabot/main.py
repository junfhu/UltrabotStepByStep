# ultrabot/main.py -- 带工具集过滤
import os
import sys
from openai import OpenAI
from ultrabot.agent import Agent
from ultrabot.tools.base import ToolRegistry
from ultrabot.tools.builtin import register_builtin_tools
from ultrabot.tools.toolsets import ToolsetManager, register_default_toolsets

# 解析简单的 --tools 参数
toolset_arg = "all"
if "--tools" in sys.argv:
    idx = sys.argv.index("--tools")
    toolset_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "all"
    
# 创建并填充工具注册表
registry = ToolRegistry()
register_builtin_tools(registry)

manager = ToolsetManager(registry)
register_default_toolsets(manager)

# 解析要使用哪些工具
active_tools = manager.resolve([toolset_arg])
print(f"Active tools: {', '.join(t.name for t in active_tools)}\n")

# 构建一个只包含活跃工具的过滤注册表
filtered_registry = ToolRegistry()
for tool in active_tools:
    filtered_registry.register(tool)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL")

agent = Agent(
    client=client,
    model=model,
    tool_registry=filtered_registry
)

print("UltraBot (Agent class). Type 'exit' to quit.\n")

while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue
    if user_input.lower() in ("exit", "quit"):
        print("Goodbye!")
        break

    # 流式输出回调在 token 到达时打印它们
    print("assistant > ", end="", flush=True)
    response = agent.run(
        user_input,
        on_content_delta=lambda chunk: print(chunk, end="", flush=True),
    )
    print("\n")
