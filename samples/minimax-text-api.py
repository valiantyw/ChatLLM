import requests
import os, dotenv

dotenv.load_dotenv()

url = f"{os.getenv('MINIMAX_BASE_URL')}/text/chatcompletion_v2"

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

headers = {
    "Content-Type": "application/json", # required
    "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY')}"
}

response = requests.post(url, json=payload, headers=headers)

data = response.json()
print(data["choices"][0]["message"]["content"])
