import json
import requests

if __package__:
    from . import ModelCapabilities, ProviderError, chat_completion, normalize_images, read_provider_settings, request_timeout, tls_verify
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from providers import ModelCapabilities, ProviderError, chat_completion, normalize_images, read_provider_settings, request_timeout, tls_verify

# 用于消除重复字符串的常量
PROVIDER_NAME = "MiniMax"
DISPLAY_ORDER = 10
ALIASES = {
    "MiniMax (Native)": PROVIDER_NAME, 
    "MiniMax (Anthropic)": PROVIDER_NAME
}
CHAT_EXTRA_BODY = {"reasoning_split": True}
_stream_mode = "auto"


MODELS = {
    PROVIDER_NAME: {
        "MiniMax-M3.1-Flash-Preview": ModelCapabilities(images=True, video=True),
        "MiniMax-M3": ModelCapabilities(images=True, video=True),
    #    "music-2.6": ModelCapabilities(kind="music", streaming=False),
        "image-01": ModelCapabilities(
            kind="image", max_count=9, streaming=False, reference_images=True,
            ratios=("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"),
        ),
    },
}
PROVIDERS = {provider: list(models) for provider, models in MODELS.items()}


def provider_settings(kind="chat"):
    """MiniMax 的聊天、图像与音乐共用同一个端点。

    ``kind`` 由注册表统一传入，以便和其他适配器保持同一套接口，
    当前不影响取值。
    """
    return read_provider_settings("MINIMAX_API_KEY", "MINIMAX_BASE_URL")


def configure_runtime(stream_mode):
    # "auto" 与 "cumulative" 行为完全相同：chat_completion 把所有非 "delta"
    # 的模式都当作「每个分片包含截至目前的完整文本」处理。
    # 之所以仍以 "auto" 为默认，是因为部分 MiniMax 部署下发累积快照，
    # 但它是一个固定选择，并非自动探测。
    global _stream_mode
    if stream_mode not in {"auto", "delta", "cumulative"}:
        raise ProviderError("MINIMAX_STREAM_MODE 必须是 auto、delta 或 cumulative")
    _stream_mode = stream_mode


DEFAULT_IMAGE_PROMPT = "一名穿白色 T 恤的男子，全身正面站姿，户外，背景是洛杉矶威尼斯海滩标志牌。90 年代纪实风格的时装摄影，胶片颗粒感，照片级写实。"
DEFAULT_MUSIC_PROMPT = "国语流行，喜庆，欢快，庆祝，新年"

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
# 1. 图像生成 - MiniMax API
# ──────────────────────────────────────────────────────────────────────── #
def _provider_error(response, payload=None):
    """把厂商返回的错误说明拼进异常，而不是只留下一个状态码。

    MiniMax 的业务错误走 HTTP 200 + ``base_resp``，网关或权限问题则是 4xx。
    两种情况都要带出 status_code、status_msg 和 Trace-Id，否则界面上只剩
    「HTTP 410」这种无法排查的信息——查日志时 Trace-Id 也是唯一凭据。
    """
    if payload is None:
        try:
            payload = response.json()
        except ValueError:
            payload = None
    code, message = None, ''
    if isinstance(payload, dict):
        base = payload.get('base_resp')
        if isinstance(base, dict):
            code = base.get('status_code')
            message = base.get('status_msg') or ''
    if not message:
        message = (response.text or '').strip()[:200]
    head = '厂商请求失败（HTTP %s%s）' % (
        response.status_code, '' if code is None else '，代码 %s' % code,
    )
    trace = response.headers.get('Trace-Id') or response.headers.get('Minimax-Request-Id')
    return ProviderError(
        head + ('：%s' % message if message else '') + ('（trace %s）' % trace if trace else '')
    )


def native_post(kind, endpoint, payload):
    api_key, base_url = provider_settings(kind=kind)
    response = requests.post(
        f'{base_url}/{endpoint}', json=payload,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        timeout=request_timeout(), verify=tls_verify(), allow_redirects=False,
    )
    try:
        if not 200 <= response.status_code < 300:
            raise _provider_error(response)
        data = response.json()
        if not isinstance(data, dict):
            raise ProviderError('厂商返回的 JSON 响应无效')
        status = data.get('base_resp') or {}
        if status.get('status_code', 0) != 0:
            raise _provider_error(response, data)
        return data
    finally:
        response.close()


def image_MiniMax(prompt=DEFAULT_IMAGE_PROMPT, model="image-01", aspect_ratio="16:9", response_format="url", n=1, prompt_optimizer=True, subject_reference=None):
    if not prompt.strip() or len(prompt) > 1500:
        raise ProviderError("图片提示词长度必须为 1-1500 个字符")
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

    return native_post("image", "image_generation", payload)


def call_image_api(prompt, model, aspect_ratio="16:9", n=1, prompt_optimizer=True, subject_reference=None):
    result = image_MiniMax(
        prompt=prompt, model=model, aspect_ratio=aspect_ratio, n=n,
        prompt_optimizer=prompt_optimizer, subject_reference=subject_reference,
    )
    data = result.get("data")
    if not isinstance(data, dict):
        raise ProviderError("MiniMax 返回的图片数据无效")
    if isinstance(data.get("image_base64"), list):
        images = [{"b64_json": encoded} for encoded in data["image_base64"]]
    else:
        images = data.get("image_urls", [])
    return {"images": normalize_images({"data": images})}


# ──────────────────────────────────────────────────────────────────────── #
# 2. 音乐生成 - MiniMax API
# ──────────────────────────────────────────────────────────────────────── #
def music_MiniMax(prompt=DEFAULT_MUSIC_PROMPT, lyrics=None, model="music-2.6", sample_rate=44100, bitrate=256000, audio_format="mp3", output_format="url", audio_url=None, audio_base64=None):
    if not lyrics:
        lyrics = DEFAULT_LYRICS
    if len(prompt) > 2000 or not 1 <= len(lyrics) <= 3500:
        raise ProviderError("音乐生成要求提示词不超过 2000 个字符、歌词为 1-3500 个字符")

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

    return native_post("music", "music_generation", payload)

# ──────────────────────────────────────────────────────────────────────── #
# 3. 文本生成（OpenAI SDK）- 聊天集成方法
# ──────────────────────────────────────────────────────────────────────── #
def call_minimax_openai(model, history, prompt, b64_data, system_prompt, **options):
    options.setdefault("extra_body", CHAT_EXTRA_BODY.copy())
    options.setdefault("stream_mode", _stream_mode)
    return chat_completion(PROVIDER_NAME, model, history, prompt, b64_data, system_prompt, **options)

call_chat_api = call_minimax_openai
call_music_api = music_MiniMax

# ──────────────────────────────────────────────────────────────────────── #
# 交互式主菜单
# ──────────────────────────────────────────────────────────────────────── #
def main():
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)

    while True:
        print("\n" + "=" * 25 + " MiniMax API 整合接口测试 " + "=" * 25)
        print("1. 文本生成 - OpenAI SDK（支持思维链分离）")
        print("2. 图像生成 - 体验文生图")
        print("3. 音乐生成 - 体验歌词编曲")
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
            prompt = input("请输入提示词 [回车使用默认: '你好，最近怎么样？']: ").strip()
            if not prompt:
                prompt = "你好，最近怎么样？"
            try:
                reply, thinking = call_minimax_openai(model="MiniMax-M3", history=[], prompt=prompt, b64_data=[], system_prompt="你是一个有用的助手。")
                if thinking:
                    print(f"Thinking:\n{thinking}\n")
                print(f"Text:\n{reply}\n")
            except Exception as e:
                print(f"调用 OpenAI API 出错：{e}")
            
        elif choice == "2":
            print("\n--- 2. 图像生成 - 体验文生图 ---")
            prompt = input("请输入图像提示词 [回车使用默认：威尼斯海滩摄影风格]: ").strip()
            if not prompt:
                prompt = DEFAULT_IMAGE_PROMPT
            n_str = input("请输入生成张数 (1-9) [回车默认: 1]: ").strip()
            n = int(n_str) if n_str.isdigit() else 1
            result = image_MiniMax(prompt=prompt, n=n)
            print(json.dumps(result, indent=4, ensure_ascii=False))
            
        elif choice == "3":
            print("\n--- 3. 音乐生成 - 体验歌词编曲 ---")
            prompt = input("请输入音乐风格提示词 [回车使用默认: '国语流行，喜庆，欢快，庆祝，新年']: ").strip()
            if not prompt:
                prompt = DEFAULT_MUSIC_PROMPT
            use_default_lyrics = input("是否使用默认新年喜庆歌词？(Y/N) [回车默认: Y]: ").strip().upper()
            lyrics = None
            if use_default_lyrics == "N":
                lyrics = input("请输入自定义歌词（支持 [Intro] [Verse] [Chorus] 等标签）: ").strip()
            result = music_MiniMax(prompt=prompt, lyrics=lyrics)
            print(json.dumps(result, indent=4, ensure_ascii=False))
            
        else:
            print("输入无效，请重新选择！")

if __name__ == "__main__":
    main()