import os, sys, json
import dotenv, requests

# Load environment variables
dotenv.load_dotenv(dotenv.find_dotenv())

# Constants for duplicate strings
DEFAULT_IMAGE_PROMPT = "A man in a white t-shirt, full-body, standing front view, outdoors, with the Venice Beach sign in the background, Los Angeles. Fashion photography in 90s documentary style, film grain, photorealistic."
DEFAULT_MUSIC_PROMPT = "Mandopop, Festive, Upbeat, Celebration, New Year"
WARNING_API_KEY_MISSING = "Warning: MINIMAX_API_KEY environment variable is not set."
MINIMAX_DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
ENV_MINIMAX_API_KEY = "MINIMAX_API_KEY"
ENV_MINIMAX_BASE_URL = "MINIMAX_BASE_URL"


# Provider models configuration
PROVIDERS = {
    "MiniMax (OpenAI)": ["MiniMax-M3", "music-2.6", "image-01"],
}

DEFAULT_LYRICS = """[Intro]
嘿！新年到！
(新年快乐！)
大家一起笑！
(哈哈！)
鞭炮声声响，锣鼓敲起来！
一，二，三，四，一起嗨！

[Verse 1]
旧的一年已经过去，烟花点亮夜空
(点亮夜空)
新的一年已经来临，充满希望和感动
家家户户贴春联，红红火火多喜庆
(多喜庆)
孩子们换上新衣裳，脸上洋溢着笑容
街头巷尾人潮汹涌，热闹非凡真开心
(真开心)
暖暖的祝福在传递，温暖了我的心
空气中弥漫着年味，饺子和汤圆香
(香喷喷)
这个时刻属于我们，一起尽情地歌唱

[Pre-Chorus]
锣鼓敲起来 鞭炮响起来
(噼里啪啦！)
笑声传过来 祝福送过来
(新年好！)
心儿跳起来 身体摆起来

[Chorus]
新年到！新年到！乐翻天！
(乐翻天！)
大家笑！大家跳！乐翻天！
(乐翻天！)
烦恼都忘掉，快乐最重要
新的一年，好运一定会来到！
新年到！新年到！乐翻天！
(乐翻天！)
舞步跳！歌声飘！乐翻天！
(乐翻天！)
祝福送给你，幸福永相依
我们一起迎接这美好的新年！

[Verse 2]
亲朋好友齐聚一堂，举杯共饮美酒
(共饮美酒)
回忆过去的美好时光，畅谈未来的追求
长辈的关怀和叮咛，晚辈的问候和拜年
(和拜年)
这份亲情的力量，让我们更加坚强
电视里播放着春晚，节目精彩又好看
(又好看)
一家人围坐在一起，温馨又充满温暖
窗外的雪花轻轻飘，大地一片银装素裹
(银装素裹)
愿这美好的时刻，永远铭刻在心窗

[Bridge]
（唱起来！）
（跳起来！）
（学起来！）
（嗨起来！）
所有的梦想，在新年里实现！
所有的烦恼，在新年里不见！
（大声喊！）
新年！新年！新年快乐！

[Chorus]
新年到！新年到！乐翻天！
(乐翻天！)
大家笑！大家跳！乐翻天！
(乐翻天！)
烦恼都忘掉，快乐最重要
新的一年，好运一定会来到！
新年到！新年到！乐翻天！
(乐翻天！)
舞步跳！歌声飘！乐翻天！
(乐翻天！)
祝福送给你，幸福永相依
我们一起迎接这美好的新年！

[Outro]
新年好！
(新年好！)
乐翻天！
(再一年！)
（新年快乐！哈哈！）
（耶！）"""

# ──────────────────────────────────────────────────────────────────────── #
# 1. Image Generation - MiniMax API
# ──────────────────────────────────────────────────────────────────────── #
def image_MiniMax(prompt=DEFAULT_IMAGE_PROMPT, model="image-01", aspect_ratio="16:9", response_format="url", n=1, prompt_optimizer=True, subject_reference=None):
    base_url = os.getenv(ENV_MINIMAX_BASE_URL, MINIMAX_DEFAULT_BASE_URL)
    url = f"{base_url}/image_generation"

    api_key = os.getenv(ENV_MINIMAX_API_KEY)
    if not api_key:
        print(WARNING_API_KEY_MISSING)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": response_format,
        "n": n,
        "prompt_optimizer": prompt_optimizer
    }

    if subject_reference:
        payload["subject_reference"] = subject_reference

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        print(f"Error calling image API: HTTP {response.status_code}")
        print(response.text)
        return None

    data = response.json()
    return data

# ──────────────────────────────────────────────────────────────────────── #
# 2. Music Generation - MiniMax API
# ──────────────────────────────────────────────────────────────────────── #
def music_MiniMax(prompt=DEFAULT_MUSIC_PROMPT, lyrics=None, model="music-2.6", sample_rate=44100, bitrate=256000, audio_format="mp3", output_format="url", audio_url=None, audio_base64=None):
    base_url = os.getenv(ENV_MINIMAX_BASE_URL, MINIMAX_DEFAULT_BASE_URL)
    url = f"{base_url}/music_generation"

    api_key = os.getenv(ENV_MINIMAX_API_KEY)
    if not api_key:
        print(WARNING_API_KEY_MISSING)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    if not lyrics:
        lyrics = DEFAULT_LYRICS

    payload = {
        "model": model,
        "prompt": prompt,
        "lyrics": lyrics,
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": bitrate,
            "format": audio_format
        },
        "output_format": output_format
    }

    if audio_url:
        payload["audio_url"] = audio_url
    if audio_base64:
        payload["audio_base64"] = audio_base64

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        print(f"Error calling music API: HTTP {response.status_code}")
        print(response.text)
        return None

    data = response.json()
    return data

# ──────────────────────────────────────────────────────────────────────── #
# 3. Text Generation (OpenAI SDK) - Chat Integration Method
# ──────────────────────────────────────────────────────────────────────── #
def call_minimax_openai(model, history, prompt, b64_data, system_prompt):
    api_key = os.getenv(ENV_MINIMAX_API_KEY)
    base_url = os.getenv("MINIMAX_OPENAI_BASE_URL")
    if not api_key:
        raise ValueError("未在环境变量中设置 MINIMAX_API_KEY")
        
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
        
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    user_content = [{"type": "text", "text": prompt}]
    for b64, mime in b64_data:
        if mime and "video" in mime.lower():
            user_content.append({
                "type": "video_url",
                "video_url": {"url": f"data:{mime};base64,{b64}"}
            })
        else:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
        
    content_payload = user_content if b64_data else prompt
    messages.append({"role": "user", "content": content_payload})
    
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        timeout=300,
        extra_body={"reasoning_split": True}
    )
    
    reply = response.choices[0].message.content or ""
    thinking = ""
    
    msg_obj = response.choices[0].message
    if hasattr(msg_obj, "reasoning_details") and msg_obj.reasoning_details:
        try:
            if isinstance(msg_obj.reasoning_details, list) and len(msg_obj.reasoning_details) > 0:
                detail = msg_obj.reasoning_details[0]
                if isinstance(detail, dict) and 'text' in detail:
                    thinking = detail['text']
                elif hasattr(detail, 'text'):
                    thinking = detail.text
                elif isinstance(detail, str):
                    thinking = detail
        except Exception:
            pass
            
    return reply, thinking

# ──────────────────────────────────────────────────────────────────────── #
# Main Interactive Menu
# ──────────────────────────────────────────────────────────────────────── #
def main():
    env_path = dotenv.find_dotenv()
    if env_path:
        dotenv.load_dotenv(env_path)
        print(f"Loaded environment variables from {env_path}")

    while True:
        print("\n" + "=" * 25 + " MiniMax API 整合接口测试 " + "=" * 25)
        print("1. 文本生成 - OpenAI SDK (支持思维链分离)")
        print("2. 图像生成 - 体验文生图 (Text-to-Image)")
        print("3. 音乐生成 - 体验歌词编曲 (Music Generation)")
        print("0. 退出程序")
        print("=" * 76)
        
        try:
            choice = input("请选择操作 [0-3]: ").strip()
        except KeyboardInterrupt:
            print("\n程序已退出。")
            break
            
        if choice == "0":
            print("感谢使用，程序已安全退出。")
            break
            
        elif choice == "1":
            print("\n--- 1. 文本生成 - OpenAI SDK ---")
            prompt = input("请输入提示词 [回车使用默认: 'Hi, how are you?']: ").strip()
            if not prompt:
                prompt = "Hi, how are you?"
            try:
                reply, thinking = call_minimax_openai(model="MiniMax-M3", history=[], prompt=prompt, b64_data=[], system_prompt="You are a helpful assistant.")
                if thinking:
                    print(f"Thinking:\n{thinking}\n")
                print(f"Text:\n{reply}\n")
            except Exception as e:
                print(f"Error calling OpenAI API: {e}")
            
        elif choice == "2":
            print("\n--- 2. 图像生成 - 体验文生图 ---")
            prompt = input("请输入图像提示词 [回车使用默认 Venice Beach 摄影风格]: ").strip()
            if not prompt:
                prompt = DEFAULT_IMAGE_PROMPT
            n_str = input("请输入生成张数 (1-9) [回车默认: 1]: ").strip()
            n = int(n_str) if n_str.isdigit() else 1
            result = image_MiniMax(prompt=prompt, n=n)
            print(json.dumps(result, indent=4, ensure_ascii=False))
            
        elif choice == "3":
            print("\n--- 3. 音乐生成 - 体验歌词编曲 ---")
            prompt = input("请输入音乐风格提示词 [回车使用默认: 'Mandopop, Festive, Upbeat, Celebration, New Year']: ").strip()
            if not prompt:
                prompt = DEFAULT_MUSIC_PROMPT
            use_default_lyrics = input("是否使用默认新年喜庆歌词？(Y/N) [回车默认: Y]: ").strip().upper()
            lyrics = None
            if use_default_lyrics == "N":
                lyrics = input("请输入自定义歌词 (支持 [Intro] [Verse] [Chorus] 等标签): ").strip()
            result = music_MiniMax(prompt=prompt, lyrics=lyrics)
            print(json.dumps(result, indent=4, ensure_ascii=False))
            
        else:
            print("输入无效，请重新选择！")

if __name__ == "__main__":
    main()