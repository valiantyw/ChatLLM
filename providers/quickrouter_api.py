# -*- coding: utf-8 -*-
"""
QuickRouter API - OpenAI compatible LLM API integration
Docs: https://doc.quickrouter.ai/
Console: https://api.quickrouter.ai/console
"""

import os, sys, json
import dotenv, requests

# Load environment variables - find .env in project directory or parent
env_path = dotenv.find_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))
if env_path:
    dotenv.load_dotenv(env_path)
    print(f"[DEBUG] Loaded .env from: {env_path}")
else:
    dotenv.load_dotenv()  # Try default locations

# Provider models configuration
PROVIDERS = {
    "QuickRouter": [
        "gpt-5.4-mini", 
        "gpt-image-2", 
        "gemini-3.1-flash-image-preview", 
    ],
}

# ───────────────────────────────────────────────────────────────────────── #
# 1. Text Generation - QuickRouter (OpenAI compatible)
# ───────────────────────────────────────────────────────────────────────── #
def text_QuickRouter(prompt="Hi, how are you?", system_prompt="You are a helpful assistant.", model="gpt-4o"):
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: Please install the 'openai' SDK first: pip install openai")
        return None

    api_key = os.getenv("QUICKROUTER_API_KEY")
    base_url = os.getenv("QUICKROUTER_BASE_URL", "https://api.quickrouter.ai/v1")
    if not api_key:
        print("Warning: QUICKROUTER_API_KEY environment variable is not set.")
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
# 2. Image Generation - QuickRouter
# ───────────────────────────────────────────────────────────────────────── #
def image_QuickRouter(prompt="A beautiful sunset over the ocean", model="dall-e-3", size="1024x1024", response_format="url"):
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: Please install the 'openai' SDK first: pip install openai")
        return None

    api_key = os.getenv("QUICKROUTER_API_KEY")
    base_url = os.getenv("QUICKROUTER_BASE_URL", "https://api.quickrouter.ai/v1")
    if not api_key:
        print("Warning: QUICKROUTER_API_KEY environment variable is not set.")
        return None

    client = OpenAI(api_key=api_key, base_url=base_url)

    response = client.images.generate(
        model=model,
        prompt=prompt,
        size=size,
        response_format=response_format,
    )

    return response.model_dump()


# ───────────────────────────────────────────────────────────────────────── #
# Core Chat Integration Method
# ───────────────────────────────────────────────────────────────────────── #
def call_quickrouter(model, history, prompt, b64_images, system_prompt):
    """
    QuickRouter chat completion function.
    
    Args:
        model: Model name (e.g., "gpt-4o", "claude-3-opus", "gemini-2.0-flash")
        history: List of previous messages [{"role": "user/assistant", "content": "..."}]
        prompt: Current user prompt
        b64_images: List of (base64_data, mime_type) tuples for image inputs
        system_prompt: System prompt to set behavior
    
    Returns:
        Tuple of (reply_text, thinking_text)
    """
    api_key = os.getenv("QUICKROUTER_API_KEY")
    base_url = os.getenv("QUICKROUTER_BASE_URL", "https://api.quickrouter.ai/v1")
    print(f"[DEBUG] QuickRouter API Key: {api_key[:15] if api_key else 'None'}...")
    print(f"[DEBUG] QuickRouter Base URL: {base_url}")
    print(f"[DEBUG] QuickRouter Model: {model}")
    if not api_key:
        raise ValueError("未在环境变量中设置 QUICKROUTER_API_KEY")
    
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    
    # Add history messages
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Build user message with image support
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
    
    # Handle case where API returns error string instead of proper response
    if isinstance(response, str):
        raise ValueError(f"QuickRouter API returned error: {response}")
    
    if not hasattr(response, 'choices') or not response.choices:
        raise ValueError(f"QuickRouter API returned invalid response: {response}")
    
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
    print("QuickRouter API - Testing Menu")
    print("=" * 50)
    
    api_key = os.getenv("QUICKROUTER_API_KEY")
    if not api_key:
        print("Please set QUICKROUTER_API_KEY in your .env file")
        return
    
    print(f"API Key: {api_key[:10]}...")
    print()
    
    # Test text generation
    print("Testing text generation...")
    result = text_QuickRouter(
        prompt="Hello, who are you?",
        system_prompt="You are a helpful assistant.",
        model="gpt-4o"
    )
    print(f"Result: {result}")


if __name__ == "__main__":
    main()