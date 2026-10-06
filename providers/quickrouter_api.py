# -*- coding: utf-8 -*-
"""
QuickRouter API - OpenAI 兼容的 LLM API 集成
文档：https://doc.quickrouter.ai/
控制台：https://api.quickrouter.ai/console
"""

import os

if __package__:
    from . import ModelCapabilities, ProviderError, capabilities, chat_completion, get_client, normalize_images, read_provider_settings
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from providers import ModelCapabilities, ProviderError, capabilities, chat_completion, get_client, normalize_images, read_provider_settings

# 用于消除重复字符串的常量
DEFAULT_SYSTEM_PROMPT = "你是一个有用的助手。"
ERROR_OPENAI_SDK = "错误：请先安装 openai SDK：pip install openai"
PROVIDER_NAME = "QuickRouter"
DISPLAY_ORDER = 20

MODELS = {
    PROVIDER_NAME: {
        "gpt-5.4-mini": ModelCapabilities(images=True),
        "gpt-image-2": ModelCapabilities(
            kind="image", max_count=10, streaming=False,
            ratios=("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"),
            sizes=("1024x1024", "1536x864", "864x1536", "1536x1152", "1152x1536", "1536x1024", "1024x1536", "1792x768"),
        ),
        "gemini-3.1-flash-image-preview": ModelCapabilities(
            kind="image", streaming=False, ratios=("1:1",), sizes=("1024x1024",),
        ),
    },
}
PROVIDERS = {provider: list(models) for provider, models in MODELS.items()}


def provider_settings(kind="chat"):
    # QuickRouter 的全部能力都走同一个 OpenAI 兼容端点。
    return read_provider_settings("QUICKROUTER_API_KEY", "QUICKROUTER_BASE_URL")


# ───────────────────────────────────────────────────────────────────────── #
# 1. 文本生成 - QuickRouter（OpenAI 兼容）
# ───────────────────────────────────────────────────────────────────────── #
def text_QuickRouter(prompt="你好，最近怎么样？", system_prompt=DEFAULT_SYSTEM_PROMPT, model="gpt-5.4-mini"):
    reply, _ = call_quickrouter(model, [], prompt, [], system_prompt)
    return {"text": reply}


# ───────────────────────────────────────────────────────────────────────── #
# 2. 图像生成 - QuickRouter
# ───────────────────────────────────────────────────────────────────────── #
def image_QuickRouter(prompt="海面上美丽的日落", model="gpt-image-2", size="1024x1024", response_format=None, n=1):
    if not prompt.strip() or len(prompt) > 32000:
        raise ProviderError("图片提示词长度必须为 1-32000 个字符")
    options = dict(model=model, prompt=prompt, size=size, n=n)
    if response_format is not None:
        options["response_format"] = response_format
    response = get_client(PROVIDER_NAME, kind="image").images.generate(**options)
    return response.model_dump()


def call_image_api(prompt, model, aspect_ratio="1:1", n=1, prompt_optimizer=True, subject_reference=None):
    spec = capabilities(PROVIDER_NAME, model)
    result = image_QuickRouter(
        prompt=prompt, model=model,
        size=dict(zip(spec.ratios, spec.sizes))[aspect_ratio], n=n,
    )
    return {"images": normalize_images(result)}


# ───────────────────────────────────────────────────────────────────────── #
# 聊天集成的核心方法
# ───────────────────────────────────────────────────────────────────────── #
def call_quickrouter(model, history, prompt, b64_images, system_prompt, **options):
    return chat_completion(PROVIDER_NAME, model, history, prompt, b64_images, system_prompt, **options)

call_chat_api = call_quickrouter


# ───────────────────────────────────────────────────────────────────────── #
# 交互式主菜单（供测试使用）
# ───────────────────────────────────────────────────────────────────────── #
def main():
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    print("QuickRouter API - 测试菜单")
    print("=" * 50)
    
    api_key = os.getenv("QUICKROUTER_API_KEY")
    if not api_key:
        print("请在 .env 文件中设置 QUICKROUTER_API_KEY")
        return
    
    
    # 测试文本生成
    print("正在测试文本生成...")
    result = text_QuickRouter(
        prompt="你好，你是谁？",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        model="gpt-5.4-mini"
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    main()