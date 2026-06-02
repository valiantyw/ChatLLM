# -*- coding: utf-8 -*-
"""
NVIDIA NIM API - NVIDIA NIM microservices for LLM API
Docs: https://docs.nvidia.com/nim/
API Catalog: https://build.nvidia.com
"""

import os, sys, json
import dotenv, requests

# Load environment variables
dotenv.load_dotenv(dotenv.find_dotenv())

# Constants for duplicate strings
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
NVIDIA_NIM_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
ENV_NVIDIA_NIM_BASE_URL = "NVIDIA_NIM_BASE_URL"


# Provider models configuration
PROVIDERS = {
    "NVIDIA NIM": ["meta/llama-4-maverick-17b-128e-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1"],
}

# ───────────────────────────────────────────────────────────────────────── #
# 1. Text Generation - NVIDIA NIM (OpenAI compatible)
# ───────────────────────────────────────────────────────────────────────── #
def text_NVIDIA(prompt="Hi, how are you?", system_prompt=DEFAULT_SYSTEM_PROMPT, model=DEFAULT_MODEL):
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: Please install the 'openai' SDK first: pip install openai")
        return None

    api_key = os.getenv("NVIDIA_API_KEY")
    base_url = os.getenv(ENV_NVIDIA_NIM_BASE_URL, NVIDIA_NIM_DEFAULT_BASE_URL)
    if not api_key:
        print("Warning: NVIDIA_API_KEY environment variable is not set.")
        return None

    client = OpenAI(api_key=api_key, base_url=base_url)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
    )

    result_text = response.choices[0].message.content or ""
    print(f"Text:\n{result_text}\n")
    return {"text": result_text}


# ───────────────────────────────────────────────────────────────────────── #
# 2. Image Generation - NVIDIA NIM (if supported)
# ───────────────────────────────────────────────────────────────────────── #
def image_NVIDIA(prompt="A beautiful sunset over the ocean", model="stable-diffusion-xl", size="1024x1024"):
    # NVIDIA NIM primarily supports LLM text models
    # Image generation may be limited - placeholder for future support
    print("Note: NVIDIA NIM image generation is not yet fully implemented.")
    print("Consider using QuickRouter or other providers for image generation.")
    return None


# ───────────────────────────────────────────────────────────────────────── #
# Core Chat Integration Method
# ───────────────────────────────────────────────────────────────────────── #
def call_nvidia_nim(model, history, prompt, b64_images, system_prompt):
    """
    NVIDIA NIM chat completion function.
    
    Args:
        model: Model name (e.g., "meta/llama-3.1-70b-instruct", "mistralai/mistral-large-2")
        history: List of previous messages [{"role": "user/assistant", "content": "..."}]
        prompt: Current user prompt
        b64_images: List of (base64_data, mime_type) tuples for image inputs
        system_prompt: System prompt to set behavior
    
    Returns:
        Tuple of (reply_text, thinking_text)
    """
    api_key = os.getenv("NVIDIA_API_KEY")
    base_url = os.getenv(ENV_NVIDIA_NIM_BASE_URL, NVIDIA_NIM_DEFAULT_BASE_URL)
    if not api_key:
        raise ValueError("未在环境变量中设置 NVIDIA_API_KEY")
    
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    
    # Add history messages
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Build user message with image support (if model supports vision)
    if b64_images:
        user_content = []
        user_content.append({"type": "text", "text": prompt})
        for b64, mime in b64_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": prompt})
    
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        timeout=300,
    )
    
    reply = response.choices[0].message.content or ""
    thinking = ""
    
    # Check for reasoning/thinking in response
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


# ───────────────────────────────────────────────────────────────────────── #
# Main Interactive Menu (for testing)
# ───────────────────────────────────────────────────────────────────────── #
def main():
    print("NVIDIA NIM API - Testing Menu")
    print("=" * 50)
    
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        print("Please set NVIDIA_API_KEY in your .env file")
        print("Get your API key from: https://build.nvidia.com/explore/discover")
        return
    
    print(f"API Key: {api_key[:10]}...")
    print()
    
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