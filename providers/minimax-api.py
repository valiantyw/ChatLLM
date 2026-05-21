import os, dotenv
import requests

dotenv.load_dotenv()

# ────────────────────────────────────────────────────────────────────────────────────────── #
def text_Anthropic():
    import anthropic

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

# ────────────────────────────────────────────────────────────────────────────────────────── #
def text_OpenAI():
    from openai import OpenAI

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

# ────────────────────────────────────────────────────────────────────────────────────────── #
def text_MiniMax():
    url = f"{os.getenv('MINIMAX_BASE_URL')}/text/chatcompletion_v2"

    headers = {
        "Content-Type": "application/json", # required
        "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY')}"
    }

    payload = {
        "model": "MiniMax-M2.7", # required. MiniMax-M2.7 ...
        "messages": [ # required. 消息内容。支持 string 和 object[] 两种类型. 
        #  string   - 消息内容. example: "你好"
        #  object[] - messages.content.type = "text" or "image_url"
            {
                "role": "system", # 设定模型的角色和行为
                "content": "你是一个有用的助手，请始终用中文回复。"
            },
        #    {"role": "assistant", "content": ""}, # 模型的历史回复，也可包含对工具的调用请求
        #    {"role": "tool", "content": ""}, # 工具调用的返回结果
            {
                "role": "user", # 用户的输入
            #    "name": "User",
                "content": [
                    {
                        "type": "text",
                        "text": "你好!"
                #    },
                #    {
                #        "type": "image_url", # 图片的公网 URL 或 Base64 编码的 Data URL
                #        "image_url": { "url": "https://cdn.hailuoai.com/prod/2024-09-18-16/user/multi_chat_file/9c0b5c14-ee88-4a5b-b503-4f626f018639.jpeg" }
                    }
                ]
            }        
        ]
    }

    response = requests.post(url, json=payload, headers=headers)

    data = response.json()
    print(data["choices"][0]["message"]["content"])

# ────────────────────────────────────────────────────────────────────────────────────────── #
def image_MiniMax():
    url = f"{os.getenv('MINIMAX_BASE_URL')}/image_generation"

    headers = {
        "Content-Type": "application/json", # 请求体的媒介类型，确保请求数据的格式为 JSON
        "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY')}"
    }

# ────────────────────────────────────────────────────────────────────────────────────────── #
def music_MiniMax():
    url = f"{os.getenv('MINIMAX_BASE_URL')}/music_generation"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY')}"
    }

# ────────────────────────────────────────────────────────────────────────────────────────── #
