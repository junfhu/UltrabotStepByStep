# ultrabot/chat.py -- 完整的多轮聊天机器人（适用于任何 OpenAI 兼容提供者）
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"), 
)
model = os.getenv("MODEL")

SYSTEM_PROMPT = """You are UltraBot, a helpful personal AI assistant.
- Answer concisely and accurately.
- When unsure, say so rather than guessing.
- Use code blocks for any code in your responses."""

# 对话历史 -- 这是核心数据结构
messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

print(f"UltraBot ready (model={model}). Type 'exit' to quit.\n")

while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue
    if user_input.lower() in ("exit", "quit"):
        print("Goodbye!")
        break

    # 1. 将用户消息追加到历史记录
    messages.append({"role": "user", "content": user_input})

    # 2. 将完整历史记录发送给 LLM
    response = client.chat.completions.create(
        model=model,
        messages=messages,
    )

    # 3. 提取助手的回复
    assistant_message = response.choices[0].message.content

    # 4. 将助手的回复追加到历史记录（这就是让对话变成
    #    "多轮"的关键 -- LLM 能看到之前所有内容）
    messages.append({"role": "assistant", "content": assistant_message})

    print(f"\nassistant > {assistant_message}\n")
