import base64
import binascii
import os
import threading
from concurrent.futures import CancelledError as TaskCancelled
from dataclasses import dataclass
from importlib import import_module
from pkgutil import iter_modules
from urllib.parse import urlparse

import httpx
import requests
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class ModelCapabilities:
    kind: str = 'chat'
    images: bool = False
    video: bool = False
    max_images: int = 4
    max_count: int = 1
    ratios: tuple = ()
    sizes: tuple = ()
    reference_images: bool = False
    streaming: bool = True
    context_budget: int = 16000


_local = threading.local()
_request_timeout = (10.0, 120.0)


def canonical_provider(provider):
    adapter = _PROVIDER_APIS.get(provider)
    return getattr(adapter, 'ALIASES', {}).get(provider, provider)


def _adapter(provider):
    try:
        return _PROVIDER_APIS[provider]
    except KeyError as error:
        raise ProviderError(f'不支持的厂商：{provider}') from error


def capabilities(provider, model):
    try:
        return MODELS[canonical_provider(provider)][model]
    except KeyError as error:
        raise ProviderError(f'不支持的厂商/模型：{provider} / {model}') from error


def configure_runtime(connect_timeout, read_timeout, stream_mode='auto'):
    global _request_timeout
    timeouts = []
    for name, value in (('connect_timeout', connect_timeout), ('read_timeout', read_timeout)):
        try:
            timeout = float(value)
            if isinstance(value, bool) or not 0 < timeout <= 3600:
                raise ValueError
        except (TypeError, ValueError) as error:
            raise ProviderError(f'{name} 必须是 0 到 3600 之间的正数') from error
        timeouts.append(timeout)
    for adapter in _ADAPTERS:
        configure = getattr(adapter, 'configure_runtime', None)
        if configure is not None:
            configure(stream_mode)
    _request_timeout = tuple(timeouts)


def provider_settings(provider, kind='chat'):
    """返回服务 ``kind`` 能力所需的 (api_key, base_url)。

    由适配器决定某项能力该用哪个端点，这样 UI 无需知道
    某个厂商的图像与聊天可能使用不同的 base URL。
    """
    return _adapter(provider).provider_settings(kind=kind)


def read_provider_settings(key_name, url_name):
    api_key = os.getenv(key_name, '').strip()
    if not api_key:
        raise ProviderError(f'缺少环境变量：{key_name}')
    base_url = os.getenv(url_name, '').strip()
    if not base_url:
        raise ProviderError(f'缺少环境变量：{url_name}')
    base_url = base_url.rstrip('/')
    parsed = urlparse(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError(f'{url_name} 必须是不含凭据、查询串和片段（fragment）的 HTTPS API 端点')
    return api_key, base_url


def request_timeout():
    return _request_timeout


def tls_verify():
    path = os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('SSL_CERT_FILE')
    if path and not os.path.isfile(path):
        raise ProviderError('配置的 CA 证书包不存在')
    return path or True


def get_client(provider, kind='chat'):
    provider = canonical_provider(provider)
    api_key, base_url = provider_settings(provider, kind=kind)
    connect_timeout, read_timeout = request_timeout()
    ca_bundle = tls_verify()
    clients = getattr(_local, 'clients', None)
    if clients is None:
        clients = _local.clients = {}
    key = (base_url, api_key, connect_timeout, read_timeout, ca_bundle)
    # 缓存键同时包含能力：同一厂商的聊天与图像可能使用不同 base URL，
    # 共用同一个槽位会导致每次调用都重建客户端。
    slot = (provider, kind)
    cached = clients.get(slot)
    if cached is not None and cached[0] == key:
        return cached[1]
    import ssl
    context = ssl.create_default_context(cafile=ca_bundle) if isinstance(ca_bundle, str) else True
    http_client = httpx.Client(verify=context)
    try:
        client = OpenAI(
            api_key=api_key, base_url=base_url, max_retries=0,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            http_client=http_client,
        )
    except Exception:
        http_client.close()
        raise
    clients[slot] = (key, client)
    if cached is not None:
        cached[1].close()
    return client


def error_message(error):
    if isinstance(error, (APITimeoutError, requests.exceptions.Timeout, httpx.TimeoutException)):
        return '请求超时，请等服务可用后重试。'
    if isinstance(error, requests.exceptions.SSLError):
        return 'TLS 证书校验失败，请配置受信任的 CA 证书包。'
    if isinstance(error, (APIConnectionError, requests.exceptions.ConnectionError, httpx.ConnectError)):
        return '连接失败，请检查网络、代理和受信任的证书。'
    if isinstance(error, APIStatusError):
        labels = {401: '认证失败', 403: '访问被拒绝', 429: '触发速率限制或配额已用尽'}
        return f"{labels.get(error.status_code, '厂商请求失败')} (HTTP {error.status_code})"
    if isinstance(error, (ProviderError, ValueError, OSError)):
        return str(error)[:300]
    return '请求异常失败，请检查配置后重试。'


def reasoning_text(message):
    for field in ('reasoning_content', 'reasoning'):
        value = getattr(message, field, None)
        if isinstance(value, str) and value:
            return value
    details = getattr(message, 'reasoning_details', None) or []
    if not isinstance(details, list):
        return ''
    parts = []
    for detail in details:
        text = detail.get('text') if isinstance(detail, dict) else getattr(detail, 'text', detail)
        if isinstance(text, str):
            parts.append(text)
    return ''.join(parts)


def chat_completion(provider, model, history, prompt, images, system_prompt, on_delta=None, cancel_event=None, *, extra_body=None, stream_mode='delta'):
    spec = capabilities(provider, model)
    if spec.kind != 'chat':
        raise ProviderError('所选模型不支持聊天')
    if images and (not (spec.images or spec.video) or len(images) > spec.max_images):
        raise ProviderError('所选模型不支持这些图片附件')
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.extend({'role': message['role'], 'content': message['content']} for message in history)
    content = [{'type': 'text', 'text': prompt or '请描述这张图片。'}] if images else prompt
    for encoded, mime in images:
        if mime and mime.startswith('image/') and spec.images:
            kind = 'image_url'
        elif mime and mime.startswith('video/') and spec.video:
            kind = 'video_url'
        else:
            raise ProviderError('当前模型不支持该附件类型')
        content.append({'type': kind, kind: {'url': f'data:{mime};base64,{encoded}'}})
    messages.append({'role': 'user', 'content': content})
    if cancel_event is not None and cancel_event.is_set():
        raise TaskCancelled()
    client = get_client(provider)
    options = {'model': model, 'messages': messages}
    if extra_body is not None:
        options['extra_body'] = extra_body
    if on_delta is not None and spec.streaming:
        reply_parts, thinking_parts = [], []
        reply_so_far, thinking_so_far = '', ''
        stream = client.chat.completions.create(**options, stream=True)
        try:
            for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise TaskCancelled()
                if not getattr(chunk, 'choices', None):
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, 'content', None) or ''
                thinking = reasoning_text(delta)
                if stream_mode != 'delta':
                    if reply_so_far and text.startswith(reply_so_far):
                        text = text[len(reply_so_far):]
                    if thinking_so_far and thinking.startswith(thinking_so_far):
                        thinking = thinking[len(thinking_so_far):]
                reply_so_far += text
                thinking_so_far += thinking
                reply_parts.append(text)
                thinking_parts.append(thinking)
                if text or thinking:
                    on_delta(text, thinking)
        finally:
            stream.close()
        reply, thinking = ''.join(reply_parts), ''.join(thinking_parts)
    else:
        response = client.chat.completions.create(**options)
        if not getattr(response, 'choices', None):
            raise ProviderError('厂商未返回任何聊天结果')
        message = response.choices[0].message
        reply, thinking = message.content or '', reasoning_text(message)
    if not reply and not thinking:
        raise ProviderError('厂商返回了空响应')
    return reply, thinking


def normalize_images(result):
    if hasattr(result, 'model_dump'):
        result = result.model_dump()
    if not isinstance(result, dict):
        raise ProviderError('厂商返回的图片数据无效')
    raw = result.get('images', result.get('data', []))
    images = []
    if not isinstance(raw, list):
        raise ProviderError('厂商返回的图片数据无效')
    for item in raw:
        if isinstance(item, str):
            item = {'url': item}
        if not isinstance(item, dict):
            continue
        url = item.get('url') or item.get('image_url') or item.get('image_file')
        encoded = item.get('b64_json')
        if isinstance(url, str) and url.startswith('data:'):
            try:
                header, encoded = url.split(',', 1)
                if ';base64' not in header or not header.startswith('data:image/'):
                    raise ValueError
            except ValueError as error:
                raise ProviderError('图片 data URL 无效') from error
            url = None
        if encoded:
            try:
                if len(encoded) > 48 * 1024 * 1024:
                    raise ProviderError('生成的图片超过大小上限')
                binary = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError, binascii.Error) as error:
                raise ProviderError('Base64 图片响应无效') from error
            images.append({'bytes': binary})
        elif isinstance(url, str) and urlparse(url).scheme == 'https' and urlparse(url).hostname:
            images.append({'url': url})
    if not images:
        raise ProviderError('厂商未返回可用图片')
    return images


def _validate_model_spec(adapter_name, model_name, model_spec):
    if not isinstance(model_name, str) or not model_name.strip():
        raise ProviderError(f'{adapter_name}：模型名必须是非空字符串')
    if not isinstance(model_spec, ModelCapabilities):
        raise ProviderError(f'{adapter_name}: {model_name} 必须是 ModelCapabilities 实例')
    ratios = tuple(model_spec.ratios)
    sizes = tuple(model_spec.sizes)
    if not all(isinstance(item, str) and item for item in ratios + sizes):
        raise ProviderError(f'{adapter_name}: {model_name} 的 ratios 与 sizes 必须是非空字符串')
    if sizes and len(sizes) != len(ratios):
        # QuickRouter 用 zip() 按位置把比例映射到尺寸，数量不匹配的话
        # 之后只会抛出裸 KeyError，而不是在启动时给出明确失败。
        raise ProviderError(f'{adapter_name}: {model_name} 必须为每个比例声明且仅声明一个尺寸')
    if model_spec.kind == 'image' and not ratios:
        raise ProviderError(f'{adapter_name}：图像模型 {model_name} 必须声明至少一个宽高比')
    for field in ('max_images', 'max_count', 'context_budget'):
        value = getattr(model_spec, field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProviderError(f'{adapter_name}: {model_name} {field} 必须是正整数')


def _build_registry(adapters):
    provider_apis, models, providers = {}, {}, {}
    for adapter in adapters:
        declared_models = getattr(adapter, 'MODELS', None)
        declared_providers = getattr(adapter, 'PROVIDERS', None)
        if not isinstance(declared_models, dict) or not isinstance(declared_providers, dict) or declared_models.keys() != declared_providers.keys():
            raise ProviderError(f'{adapter.__name__}：MODELS 与 PROVIDERS 必须声明相同的厂商')
        for provider, names in declared_providers.items():
            model_specs = declared_models[provider]
            if not isinstance(provider, str) or not provider.strip() or not isinstance(model_specs, dict) or not model_specs:
                raise ProviderError(f'{adapter.__name__}：每个厂商都必须有名称和至少一个模型')
            if not isinstance(names, (list, tuple)) or list(model_specs) != list(names):
                raise ProviderError(f'{adapter.__name__}：模型列表与 MODELS 不一致：{provider}')
            if provider in provider_apis:
                raise ProviderError(f'厂商名重复：{provider}')
            for model_name, model_spec in model_specs.items():
                _validate_model_spec(adapter.__name__, model_name, model_spec)
            provider_apis[provider] = adapter
            models[provider] = model_specs
            providers[provider] = list(names)
    if not providers:
        raise ProviderError('未找到任何带模型的厂商')
    for adapter in adapters:
        aliases = getattr(adapter, 'ALIASES', {})
        if not isinstance(aliases, dict):
            raise ProviderError(f'{adapter.__name__}：ALIASES 必须是映射')
        for alias, target in aliases.items():
            if not isinstance(alias, str) or not alias.strip() or not isinstance(target, str) or target not in adapter.MODELS:
                raise ProviderError(f'{adapter.__name__}：别名的目标必须是同一适配器声明的厂商')
            if alias in provider_apis:
                raise ProviderError(f'厂商名或别名重复：{alias}')
            provider_apis[alias] = adapter
    return provider_apis, models, providers


_ADAPTERS = tuple(sorted(
    (
        import_module(f'.{module.name}', __name__)
        for module in iter_modules(__path__)
        if not module.ispkg and module.name.endswith('_api')
    ),
    key=lambda adapter: (getattr(adapter, 'DISPLAY_ORDER', 100), adapter.__name__),
))
_PROVIDER_APIS, MODELS, PROVIDERS = _build_registry(_ADAPTERS)

def _default_model(kind):
    return next((model for model, spec in MODELS[DEFAULT_PROVIDER].items() if spec.kind == kind), None)


DEFAULT_PROVIDER = next(iter(PROVIDERS))
DEFAULT_MODEL = _default_model('chat') or PROVIDERS[DEFAULT_PROVIDER][0]

# 放在上述常量之后定义，确保每个导出名都已存在。
__all__ = [
    "PROVIDERS", "MODELS", "ModelCapabilities", "ProviderError",
    "DEFAULT_PROVIDER", "DEFAULT_MODEL",
    "call_chat_api", "call_image_api", "call_music_api",
    "capabilities", "canonical_provider", "chat_completion", "configure_runtime",
    "error_message", "get_client", "normalize_images", "provider_settings",
    "read_provider_settings", "request_timeout", "tls_verify",
]


_KIND_LABELS = {'chat': '聊天', 'image': '图像生成', 'music': '音乐生成'}


def _api(provider, kind):
    handler = getattr(_adapter(provider), f'call_{kind}_api', None)
    if handler is None:
        raise ProviderError(f'{_KIND_LABELS.get(kind, kind)}不受支持，厂商：{provider}')
    return handler


def call_chat_api(provider, model, history, prompt, b64_images, system_prompt, **options):
    return _api(provider, 'chat')(model, history, prompt, b64_images, system_prompt, **options)

def call_image_api(provider, prompt, model, aspect_ratio="16:9", n=1, prompt_optimizer=True, subject_reference=None):
    spec = capabilities(provider, model)
    if spec.kind != "image" or aspect_ratio not in spec.ratios:
        raise ProviderError(f"不支持的图片宽高比：{model}: {aspect_ratio}")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= spec.max_count:
        raise ProviderError(f"{model} 每次请求支持 1 到 {spec.max_count} 张图片")
    if subject_reference and not spec.reference_images:
        raise ProviderError(f"{model} 在本应用中不支持参考图")
    return _api(provider, 'image')(
        prompt=prompt, model=model, aspect_ratio=aspect_ratio, n=n,
        prompt_optimizer=prompt_optimizer, subject_reference=subject_reference,
    )

def call_music_api(provider, prompt, lyrics, model, sample_rate):
    if capabilities(provider, model).kind != "music":
        raise ProviderError(f"以下厂商不支持音乐生成：{provider} / {model}")
    return _api(provider, 'music')(
        prompt=prompt, lyrics=lyrics, model=model, sample_rate=sample_rate,
    )
