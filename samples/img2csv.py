# requirments
#    > pip install openai
#    > pip install google-genai
#    > pip install anthropic

# imported modules
import os, sys, base64

# LLM authenticate
LLM_GATEWAY_KEY = "c4bcdc467d7e4beaa8b1ad53fc18bdb9"
LLM_GATEWAY_USR = "vwang"

# OpenAI
def LLM_OpenAI(b64, image_ext, prompts):
    from openai import OpenAI

    mime_type = "image/png" if "png" in image_ext.lower() else "image/jpeg"

    client = OpenAI(
        base_url="https://llm-api.amd.com/OpenAI",
        api_key="dummy",
        default_headers={
            "Ocp-Apim-Subscription-Key": LLM_GATEWAY_KEY,
            "user": LLM_GATEWAY_USR
        }
    )

    resp_b64 = client.chat.completions.create(
        # Model Name: gpt-5 | Deployment ID: pdue-aoai-002-gpt-5
        # Model Name: gpt-5.1 | Deployment ID: pdue-aoai-002-gpt-5.1
        model="gpt-5.1",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompts[0]},
                    {"type": "text", "text": prompts[1]},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}
                ]
            }
        ]
    )
    
    return resp_b64.choices[0].message.content

# Google GenAI
def LLM_Google(b64, image_ext, prompts):
    from google import genai
    from google.genai import types
    from google.genai.types import HttpOptions

    mime_type = "image/png" if "png" in image_ext.lower() else "image/jpeg"

    client = genai.Client(
        vertexai=True,
        api_key="dummy",
        http_options=HttpOptions(
            base_url="https://llm-api.amd.com/VertexGen",
            api_version="v1",
            headers={
                "Ocp-Apim-Subscription-Key": LLM_GATEWAY_KEY,
                "user": LLM_GATEWAY_USR
            }
        )
    )

    resp_b64 = client.models.generate_content(
        # Model Name: gemini-2.5-pro | Deployment ID: gemini-2.5-pro
        # Model Name: gemini-3-pro-preview | Deployment ID: gemini-3-pro-preview
        model="gemini-3-pro-preview",
        contents=[
            types.Content(
                parts=[
                    types.Part(text=prompts[0]),
                    types.Part(text=prompts[1]),
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=mime_type,
                            data=base64.b64decode(b64),
                        ),
                        media_resolution={"level": "media_resolution_high"}
                    )
                ]
            )
        ]
    )

    return resp_b64.text

# Anthropic
def LLM_Anthropic(b64, image_ext):
    from anthropic import Anthropic
    client = Anthropic(
        base_url="https://llm-api.amd.com/Anthropic",
        api_key="dummy",
        default_headers={
            "Ocp-Apim-Subscription-Key": LLM_GATEWAY_KEY,
            "user": LLM_GATEWAY_USR,
            "anthropic-version": "2023-10-16"
        }
    )

    response = client.messages.create(
        # Model Name: Claude-Sonnet-4.5 | Deployment ID: claude-sonnet-4-5
        model="claude-sonnet-4-5",
        max_tokens=200,
        temperature=0.7,
        messages=[
            {"role": "user", "content": "你是谁?"}
        ]
    )
    
    return response.content

# 图片 base64 编码
def img_base64(image_path):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return b64

# 转换图片为 CSV
def convert_img_2_csv(image_path, image_ext, output_csv):
    print(f"Processing image: {image_path}")
    if not os.path.exists(image_path):
        print(f"Error: File not found: {image_path}")
        return

    # 图片 base64
    img_b64 = img_base64(image_path)

    prompts = [
        "提取图片中的表格，输出 csv 格式。",
        "输出是一个合并后的表（把图片里所有分栏合并为一列式的记录），字段为：分数段, 同分人数, 累计人数。"
    ]

    # LLM API
#    result = LLM_OpenAI(img_b64, image_ext, prompts)
    result = LLM_Google(img_b64, image_ext, prompts)
#    result = LLM_Anthropic(img_b64, image_ext)

    with open(output_csv, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"Generated CSV file: {output_csv}")


#
if __name__ == "__main__":
    if len(sys.argv) > 1:
        img_path = sys.argv[1]

        # Determine output path
        # If input is in Data-Original, output to Data-Processed
        dir_name, img_name = os.path.split(img_path)
        base_name, img_ext = os.path.splitext(img_name)

        # Check if we are in the project root
        project_root = os.getcwd()
        processed_dir = os.path.join(project_root, 'Data-Processed')
        if not os.path.exists(processed_dir):
            os.makedirs(processed_dir)

        output_csv = os.path.join(processed_dir, base_name + '.csv')
        convert_img_2_csv(img_path, img_ext, output_csv)

    else:
        print("Usage: python Tools\\img2csv.py <img_path>")