# ultrabot/main.py -- 带工具的智能体
import os
from openai import OpenAI
from ultrabot.agent import Agent
from ultrabot.tools.base import ToolRegistry
from ultrabot.tools.builtin import register_builtin_tools

# 创建并填充工具注册表
registry = ToolRegistry()
register_builtin_tools(registry)

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL")

agent = Agent(
    client=client,
    model=model,
    tool_registry=registry
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
