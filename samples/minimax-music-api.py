import requests, json
import os, dotenv

dotenv.load_dotenv(dotenv.find_dotenv())

url = f"{os.getenv('MINIMAX_BASE_URL')}/music_generation"

payload = {
    "model": "music-2.6", # reqired. music-2.6, music-cover
    "prompt": "Mandopop, Festive, Upbeat, Celebration, New Year", # optional. max string length: 2000
#    "lyrics": "[Intro]\n嘿！新年到！\n(新年快乐！)\n大家一起笑！\n(哈哈！)\n鞭炮声声响，锣鼓敲起来！\n一，二，三，四，一起嗨！\n\n[Verse 1]\n旧的一年已经过去，烟花点亮夜空\n(点亮夜空)\n新的一年已经来临，充满希望和感动\n家家户户贴春联，红红火火多喜庆\n(多喜庆)\n孩子们换上新衣裳，脸上洋溢着笑容\n街头巷尾人潮涌，热闹非凡真开心\n(真开心)\n暖暖的祝福在传递，温暖了我的心\n空气中弥漫着年味，饺子和汤圆香\n(香喷喷)\n这个时刻属于我们，一起尽情地歌唱\n\n[Pre-Chorus]\n锣鼓敲起来 鞭炮响起来\n(噼里啪啦！)\n笑声传过来 祝福送过来\n(新年好！)\n心儿跳起来 身体摆起来\n\n[Chorus]\n新年到！新年到！乐翻天！\n(乐翻天！)\n大家笑！大家跳！乐翻天！\n(乐翻天！)\n烦恼都忘掉，快乐最重要\n新的一年，好运一定会来到！\n新年到！新年到！乐翻天！\n(乐翻天！)\n舞步跳！歌声飘！乐翻天！\n(乐翻天！)\n祝福送给你，幸福永相依\n我们一起迎接这美好的新年！\n\n[Verse 2]\n亲朋好友齐聚一堂，举杯共饮美酒\n(共饮美酒)\n回忆过去的美好时光，畅谈未来的追求\n长辈的关怀和叮咛，晚辈的问候和拜年\n(和拜年)\n这份亲情的力量，让我们更加坚强\n电视里播放着春晚，节目精彩又好看\n(又好看)\n一家人围坐在一起，温馨又充满温暖\n窗外的雪花轻轻飘，大地一片银装素裹\n(银装素裹)\n愿这美好的时刻，永远铭刻在心窝\n\n[Bridge]\n（唱起来！）\n（跳起来！）\n（笑起来！）\n（嗨起来！）\n所有的梦想，在新的一年里实现！\n所有的烦恼，在新的一年里不见！\n（大声喊！）\n新年！新年！新年快乐！\n\n[Chorus]\n新年到！新年到！乐翻天！\n(乐翻天！)\n大家笑！大家跳！乐翻天！\n(乐翻天！)\n烦恼都忘掉，快乐最重要\n新的一年，好运一定会来到！\n新年到！新年到！乐翻天！\n(乐翻天！)\n舞步跳！歌声飘！乐翻天！\n(乐翻天！)\n祝福送给你，幸福永相依\n我们一起迎接这美好的新年！\n\n[Outro]\n新年好！\n(新年好！)\n乐翻天！\n(再一年！)\n（新年快乐！哈哈！）\n（耶！）", # optional. string length: 1 - 3500
    "audio_setting": {
        "sample_rate": 44100, # 16000, 24000, 32000, 44100
        "bitrate": 256000,    # 32000, 64000, 128000, 256000
        "format": "mp3"       # mp3, wav, pcm
    },
    "output_format": "url", # optional. url or hex, default is hex

# input audio, only for music-cover model
#   "audio_url": "https://example.com/input.mp3", or
#   "audio_base64": 
#   legth: 6s ~ 6min, size: <=50MB, audio format: mp3, wav, flac ...
}

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY')}"
}

response = requests.post(url, json=payload, headers=headers)
# data.status: 1 -> processing, 2 -> success
# data.audio: if output_format is url, data.audio is the download url; 
#             if output_format is hex, data.audio is the hex string of audio file content.
# base_resp.status_code: 0 -> success https://platform.minimaxi.com/docs/api-reference/music-generation#response-base-resp-status-code

result = response.json()
print(json.dumps(result, indent=4, ensure_ascii=False))
