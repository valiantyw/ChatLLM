# -*- coding: utf-8 -*-
"""
QuickRouter API - OpenAI compatible LLM API integration
Docs: https://doc.quickrouter.ai/
Console: https://api.quickrouter.ai/console
"""

import os

if __package__:
    from . import ModelCapabilities, ProviderError, capabilities, chat_completion, get_client, normalize_images, read_provider_settings
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from providers import ModelCapabilities, ProviderError, capabilities, chat_completion, get_client, normalize_images, read_provider_settings

# Constants for duplicate strings
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
ERROR_OPENAI_SDK = "Error: Please install the 'openai' SDK first: pip install openai"
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


def provider_settings(native=False):
    return read_provider_settings("QUICKROUTER_API_KEY", "QUICKROUTER_BASE_URL")


# ───────────────────────────────────────────────────────────────────────── #
# 1. Text Generation - QuickRouter (OpenAI compatible)
# ───────────────────────────────────────────────────────────────────────── #
def text_QuickRouter(prompt="Hi, how are you?", system_prompt=DEFAULT_SYSTEM_PROMPT, model="gpt-5.4-mini"):
    reply, _ = call_quickrouter(model, [], prompt, [], system_prompt)
    return {"text": reply}


# ───────────────────────────────────────────────────────────────────────── #
# 2. Image Generation - QuickRouter
# ───────────────────────────────────────────────────────────────────────── #
def image_QuickRouter(prompt="A beautiful sunset over the ocean", model="gpt-image-2", size="1024x1024", response_format=None, n=1):
    if not prompt.strip() or len(prompt) > 32000:
        raise ProviderError("Image prompt must contain 1-32000 characters")
    options = dict(model=model, prompt=prompt, size=size, n=n)
    if response_format is not None:
        options["response_format"] = response_format
    response = get_client(PROVIDER_NAME).images.generate(**options)
    return response.model_dump()


def call_image_api(prompt, model, aspect_ratio="1:1", n=1, prompt_optimizer=True, subject_reference=None):
    spec = capabilities(PROVIDER_NAME, model)
    result = image_QuickRouter(
        prompt=prompt, model=model,
        size=dict(zip(spec.ratios, spec.sizes))[aspect_ratio], n=n,
    )
    return {"images": normalize_images(result)}


# ───────────────────────────────────────────────────────────────────────── #
# Core Chat Integration Method
# ───────────────────────────────────────────────────────────────────────── #
def call_quickrouter(model, history, prompt, b64_images, system_prompt, **options):
    return chat_completion(PROVIDER_NAME, model, history, prompt, b64_images, system_prompt, **options)

call_chat_api = call_quickrouter


# ───────────────────────────────────────────────────────────────────────── #
# Main Interactive Menu (for testing)
# ───────────────────────────────────────────────────────────────────────── #
def main():
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    print("QuickRouter API - Testing Menu")
    print("=" * 50)
    
    api_key = os.getenv("QUICKROUTER_API_KEY")
    if not api_key:
        print("Please set QUICKROUTER_API_KEY in your .env file")
        return
    
    
    # Test text generation
    print("Testing text generation...")
    result = text_QuickRouter(
        prompt="Hello, who are you?",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        model="gpt-5.4-mini"
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    main()