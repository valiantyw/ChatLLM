from .minimax_api import (
    call_minimax_openai, image_MiniMax, music_MiniMax,
    PROVIDERS as MINIMAX_PROVIDERS,
)
from .quickrouter_api import (
    call_quickrouter, image_QuickRouter,
    PROVIDERS as QUICKROUTER_PROVIDERS,
)
from .nvidia_nim_api import (
    call_nvidia_nim, image_NVIDIA,
    PROVIDERS as NVIDIA_NIM_PROVIDERS,
)

# Merge all PROVIDERS into one dictionary
PROVIDERS = {}
PROVIDERS.update(MINIMAX_PROVIDERS)
PROVIDERS.update(QUICKROUTER_PROVIDERS)
PROVIDERS.update(NVIDIA_NIM_PROVIDERS)

# Agnostic constants for defaults
DEFAULT_PROVIDER = "MiniMax (OpenAI)"
DEFAULT_MODEL = "MiniMax-M3"
SHORT_TITLE_PROVIDER = DEFAULT_PROVIDER
SHORT_TITLE_MODEL = "MiniMax-M2.7"
MUSIC_MODEL = "music-2.6"
DEFAULT_IMAGE_MODEL = "image-01"

def is_image_model(model):
    """Check if a model is an image generation model."""
    if not model:
        return False
    model_lower = model.lower()
    return "image" in model_lower or model_lower in ["image-01", "gpt-image-2", "gemini-3.1-flash-image-preview"]

def is_music_model(model):
    """Check if a model is a music generation model."""
    if not model:
        return False
    return model == "music-2.6"

def call_chat_api(provider, model, history, prompt, b64_images, system_prompt):
    """Generic chat completion API router."""
    if provider in ["MiniMax (Native)", DEFAULT_PROVIDER, "MiniMax (Anthropic)"]:
        return call_minimax_openai(model, history, prompt, b64_images, system_prompt)
    elif provider == "QuickRouter":
        return call_quickrouter(model, history, prompt, b64_images, system_prompt)
    elif provider == "NVIDIA NIM":
        return call_nvidia_nim(model, history, prompt, b64_images, system_prompt)
    else:
        raise ValueError(f"????API???: {provider}")

def call_image_api(provider, prompt, model, aspect_ratio="16:9", n=1, prompt_optimizer=True, subject_reference=None):
    """Generic image generation API router."""
    if provider == 'QuickRouter':
        size_map = {
            '1:1': '1024x1024',
            '16:9': '1792x1024',
            '9:16': '1024x1792',
            '4:3': '1024x768',
            '3:4': '768x1024'
        }
        size = size_map.get(aspect_ratio, '1024x1024')
        return image_QuickRouter(
            prompt=prompt,
            model=model,
            size=size,
            response_format='url'
        )
    elif provider == 'NVIDIA NIM':
        size_map = {
            '1:1': '1024x1024',
            '16:9': '1792x1024',
            '9:16': '1024x1792',
            '4:3': '1024x768',
            '3:4': '768x1024'
        }
        size = size_map.get(aspect_ratio, '1024x1024')
        return image_NVIDIA(
            prompt=prompt,
            model=model,
            size=size
        )
    else:
        return image_MiniMax(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            n=n,
            prompt_optimizer=prompt_optimizer,
            subject_reference=subject_reference
        )

def call_music_api(provider, prompt, lyrics, model, sample_rate):
    """Generic music generation API router."""
    # Currently only MiniMax supports music generation
    return music_MiniMax(
        prompt=prompt,
        lyrics=lyrics,
        model=model,
        sample_rate=sample_rate,
        bitrate=256000,
        audio_format="mp3",
        output_format="url"
    )
