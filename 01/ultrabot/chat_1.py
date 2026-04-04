# ultrabot/chat.py -- 你的第一次 AI 对话
import os
from openai import OpenAI

# 三个环境变量控制你与哪个 LLM 对话：
#   OPENAI_API_KEY  -- 你的 API 密钥
#   OPENAI_BASE_URL -- 提供者的基础 URL
#   MODEL           -- 模型名称

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"), 
)
model = os.getenv("MODEL")

response = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
