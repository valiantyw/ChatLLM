import os, dotenv

dotenv.load_dotenv()

class MiniMaxAPI:
    def __init__(self):
        self.base_url = os.getenv('MINIMAX_BASE_URL')
        self.api_key = os.getenv('MINIMAX_API_KEY')

    def chat_completion(self, model, messages):
        url = f"{self.base_url}/text/chatcompletion_v2"
        payload = {
            "model": model,
            "messages": messages
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        response = requests.post(url, json=payload, headers=headers)
        return response.json()
        