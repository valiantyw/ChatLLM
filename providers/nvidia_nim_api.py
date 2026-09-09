# -*- coding: utf-8 -*-
"""
NVIDIA NIM API - NVIDIA NIM microservices for LLM API
Docs: https://docs.nvidia.com/nim/
API Catalog: https://build.nvidia.com
"""

import os

if __package__:
    from . import ModelCapabilities, ProviderError, chat_completion, read_provider_settings
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from providers import ModelCapabilities, ProviderError, chat_completion, read_provider_settings

# Constants for duplicate strings
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
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


def provider_settings(native=False):
    return read_provider_settings("NVIDIA_API_KEY", "NVIDIA_NIM_BASE_URL")


# ───────────────────────────────────────────────────────────────────────── #
# 1. Text Generation - NVIDIA NIM (OpenAI compatible)
# ───────────────────────────────────────────────────────────────────────── #
def text_NVIDIA(prompt="Hi, how are you?", system_prompt=DEFAULT_SYSTEM_PROMPT, model=DEFAULT_MODEL):
    reply, _ = call_nvidia_nim(model, [], prompt, [], system_prompt)
    return {"text": reply}


# ───────────────────────────────────────────────────────────────────────── #
# 2. Image Generation - NVIDIA NIM (if supported)
# ───────────────────────────────────────────────────────────────────────── #
def image_NVIDIA(prompt="A beautiful sunset over the ocean", model="stable-diffusion-xl", size="1024x1024"):
    raise ProviderError("NVIDIA NIM image generation is not supported by this application")


# ───────────────────────────────────────────────────────────────────────── #
# Core Chat Integration Method
# ───────────────────────────────────────────────────────────────────────── #
def call_nvidia_nim(model, history, prompt, b64_images, system_prompt, **options):
    return chat_completion(PROVIDER_NAME, model, history, prompt, b64_images, system_prompt, **options)

call_chat_api = call_nvidia_nim


# ───────────────────────────────────────────────────────────────────────── #
# Main Interactive Menu (for testing)
# ───────────────────────────────────────────────────────────────────────── #
def main():
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    print("NVIDIA NIM API - Testing Menu")
    print("=" * 50)
    
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        print("Please set NVIDIA_API_KEY in your .env file")
        print("Get your API key from: https://build.nvidia.com/explore/discover")
        return
    
    
    # Test text generation
    print("Testing text generation...")
    result = text_NVIDIA(
        prompt="Hello, who are you?",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        model=DEFAULT_MODEL
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    main()