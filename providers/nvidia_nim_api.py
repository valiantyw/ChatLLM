# -*- coding: utf-8 -*-
"""
NVIDIA NIM API - 面向 LLM API 的 NVIDIA NIM 微服务
文档：https://docs.nvidia.com/nim/
API 目录：https://build.nvidia.com
"""

import os

if __package__:
    from . import ModelCapabilities, ProviderError, chat_completion, read_provider_settings
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from providers import ModelCapabilities, ProviderError, chat_completion, read_provider_settings

# 用于消除重复字符串的常量
DEFAULT_SYSTEM_PROMPT = "你是一个有用的助手。"
DEFAULT_MODEL = "meta/llama-4-maverick-17b-128e-instruct"
PROVIDER_NAME = "NVIDIA NIM"
DISPLAY_ORDER = 30


MODELS = {
    PROVIDER_NAME: {
        "meta/llama-4-maverick-17b-128e-instruct": ModelCapabilities(images=True),
        "nvidia/llama-3.3-nemotron-super-49b-v1": ModelCapabilities(),
    },
}
PROVIDERS = {provider: list(models) for provider, models in MODELS.items()}


def provider_settings(kind="chat"):
    # 本应用只使用 NVIDIA NIM 的聊天能力。
    return read_provider_settings("NVIDIA_API_KEY", "NVIDIA_NIM_BASE_URL")


# ───────────────────────────────────────────────────────────────────────── #
# 1. 文本生成 - NVIDIA NIM（OpenAI 兼容）
# ───────────────────────────────────────────────────────────────────────── #
def text_NVIDIA(prompt="你好，最近怎么样？", system_prompt=DEFAULT_SYSTEM_PROMPT, model=DEFAULT_MODEL):
    reply, _ = call_nvidia_nim(model, [], prompt, [], system_prompt)
    return {"text": reply}


# ───────────────────────────────────────────────────────────────────────── #
# 聊天集成的核心方法
# ───────────────────────────────────────────────────────────────────────── #
def call_nvidia_nim(model, history, prompt, b64_images, system_prompt, **options):
    return chat_completion(PROVIDER_NAME, model, history, prompt, b64_images, system_prompt, **options)

call_chat_api = call_nvidia_nim


# ───────────────────────────────────────────────────────────────────────── #
# 交互式主菜单（供测试使用）
# ───────────────────────────────────────────────────────────────────────── #
def main():
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    print("NVIDIA NIM API - 测试菜单")
    print("=" * 50)
    
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        print("请在 .env 文件中设置 NVIDIA_API_KEY")
        print("获取 API key：https://build.nvidia.com/explore/discover")
        return
    
    
    # 测试文本生成
    print("正在测试文本生成...")
    result = text_NVIDIA(
        prompt="你好，你是谁？",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        model=DEFAULT_MODEL
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    main()