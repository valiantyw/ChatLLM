# Dependencies (install via pip before running):
#   pip install anthropic
#
# Config Environment Variable
#   MINIMAX_ANTHROPIC_BASE_URL: https://api.minimaxi.com/anthropic

import anthropic
import os, dotenv

dotenv.load_dotenv()

client = anthropic.Client(api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_ANTHROPIC_BASE_URL"))

message = client.messages.create(
    model="MiniMax-M2.7",
    max_tokens=1000, # 最大生成 token 数
    system="You are a helpful assistant.", # 系统提示词
    messages=[ # 支持文本和工具调用，不支持图像和文档输入
        {
            "role": "user",
            "content": [
                {
                    "type": "text", # 字段类型. text -> 文本消息, thinking -> 推理的内容, image/document -> 不支持的输入类型
                    "text": "Hi, how are you?"
                }
            ]
        }
    ]
)

for block in message.content:
    if block.type == "thinking":
        print(f"Thinking:\n{block.thinking}\n")
    elif block.type == "text":
        print(f"Text:\n{block.text}\n")
