# chat_stream.py -- 流式输出版本
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
model = os.getenv("MODEL")

SYSTEM_PROMPT = """You are UltraBot, a helpful personal AI assistant."""

messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

print("UltraBot (streaming). Type 'exit' to quit.\n")

while True:
    user_input = input("you > ").strip()
    if not user_input:
        continue
    if user_input.lower() in ("exit", "quit"):
        break

    messages.append({"role": "user", "content": user_input})

    # stream=True 返回一个 chunk 迭代器，而不是一个完整的响应
    print("assistant > ", end="", flush=True)
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,  # <-- 关键参数
    )

    # 在流式输出的同时收集完整响应
    full_response = ""
    for chunk in stream:
        # 每个 chunk 有一个 delta，包含一小段内容
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end="", flush=True)
            full_response += delta.content

    print("\n")  # 流式输出完成后换行

    messages.append({"role": "assistant", "content": full_response})
