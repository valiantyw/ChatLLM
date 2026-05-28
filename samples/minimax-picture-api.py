import requests
import os, dotenv

dotenv.load_dotenv(dotenv.find_dotenv())

url = f"{os.getenv('MINIMAX_BASE_URL')}/image_generation"

payload_text = { # 文生图
    "model": "image-01",
    "prompt": "A man in a white t-shirt, full-body, standing front view, outdoors, with the Venice Beach sign in the background, Los Angeles. Fashion photography in 90s documentary style, film grain, photorealistic.",
    "aspect_ratio": "16:9", # 图像宽高比，默认为 1:1，可选值包括 1:1、16:9、9:16、4:3、3:4、2:3、21:9
    "response_format": "url", # 返回图片的形式，默认为 url。可选值：url, base64。
    "n": 3, # 单次请求生成的图片数量，取值范围[1, 9]，默认为 1
    "prompt_optimizer": True, # 是否开启 prompt 自动优化，默认为 false.
}

payload_image = { # 图生图
    "model": "image-01",
    "prompt": "A girl looking into the distance from a library window",
    "aspect_ratio": "16:9",
    "subject_reference": [
        {
            # 目前仅支持 character 类型，表示对图像中人物主体的参考
            "type": "character",
            # 参考图文件。支持公网 URL 或 Base64 编码的 Data URL (data:image/jpeg;base64,...) 为获得最佳效果，请上传单人正面照片
            #   格式：JPG, JPEG, PNG
            #   大小：小于 10MB
            "image_file": "https://cdn.hailuoai.com/prod/2025-08-12-17/video_cover/1754990600020238321-411603868533342214-cover.jpg"
        }
    ],
    "n": 2
}

headers = {
    "Content-Type": "application/json", # 请求体的媒介类型，请设置为 application/json 确保请求数据的格式为 JSON
    "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY')}"
}

response = requests.post(url, json=payload_text, headers=headers)

print(response.text)

