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
