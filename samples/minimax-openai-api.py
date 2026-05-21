# Dependencies (install via pip before running):
#   pip install openai
#
# Config Environment Variable
#   MINIMAX_OPENAI_BASE_URL: https://api.minimaxi.com/v1

import os, dotenv
from openai import OpenAI

dotenv.load_dotenv()

client = OpenAI(api_key=os.getenv("MINIMAX_API_KEY"), base_url=os.getenv("MINIMAX_OPENAI_BASE_URL"))

response = client.chat.completions.create(
    model="MiniMax-M2.7",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi, how are you?"},
    ],
    # 设置 reasoning_split=True 将思考内容分离到 reasoning_details 字段
    extra_body={"reasoning_split": True},
)

print(f"Thinking:\n{response.choices[0].message.reasoning_details[0]['text']}\n")
print(f"Text:\n{response.choices[0].message.content}\n")
print(response.choices[0].message.content)
