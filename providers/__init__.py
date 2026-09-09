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
        raise ProviderError(f'Unsupported provider: {provider}') from error


def capabilities(provider, model):
    try:
        return MODELS[canonical_provider(provider)][model]
    except KeyError as error:
        raise ProviderError(f'Unsupported provider/model: {provider} / {model}') from error


def configure_runtime(connect_timeout, read_timeout, stream_mode='auto'):
    global _request_timeout
    timeouts = []
    for name, value in (('connect_timeout', connect_timeout), ('read_timeout', read_timeout)):
        try:
            timeout = float(value)
            if isinstance(value, bool) or not 0 < timeout <= 3600:
                raise ValueError
        except (TypeError, ValueError) as error:
            raise ProviderError(f'{name} must be a positive number no greater than 3600') from error
        timeouts.append(timeout)
    for adapter in _ADAPTERS:
        configure = getattr(adapter, 'configure_runtime', None)
        if configure is not None:
            configure(stream_mode)
    _request_timeout = tuple(timeouts)


def provider_settings(provider, native=False):
    return _adapter(provider).provider_settings(native=native)


def read_provider_settings(key_name, url_name):
    api_key = os.getenv(key_name, '').strip()
    if not api_key:
        raise ProviderError(f'Missing environment variable: {key_name}')
    base_url = os.getenv(url_name, '').strip()
    if not base_url:
        raise ProviderError(f'Missing environment variable: {url_name}')
    base_url = base_url.rstrip('/')
    parsed = urlparse(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError(f'{url_name} must be an HTTPS API endpoint without credentials, query, or fragment')
    return api_key, base_url


def request_timeout():
    return _request_timeout


def tls_verify():
    path = os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('SSL_CERT_FILE')
    if path and not os.path.isfile(path):
        raise ProviderError('Configured CA bundle does not exist')
    return path or True


def get_client(provider):
    provider = canonical_provider(provider)
    api_key, base_url = provider_settings(provider)
    connect_timeout, read_timeout = request_timeout()
    ca_bundle = tls_verify()
    clients = getattr(_local, 'clients', None)
    if clients is None:
        clients = _local.clients = {}
    key = (base_url, api_key, connect_timeout, read_timeout, ca_bundle)
    cached = clients.get(provider)
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
    clients[provider] = (key, client)
    if cached is not None:
        cached[1].close()
    return client


def error_message(error):
    if isinstance(error, (APITimeoutError, requests.exceptions.Timeout, httpx.TimeoutException)):
        return 'Request timed out. Retry when the service is available.'
    if isinstance(error, requests.exceptions.SSLError):
        return 'TLS certificate verification failed. Configure a trusted CA bundle.'
    if isinstance(error, (APIConnectionError, requests.exceptions.ConnectionError, httpx.ConnectError)):
        return 'Connection failed. Check the network, proxy, and trusted certificates.'
    if isinstance(error, APIStatusError):
        labels = {401: 'Authentication failed', 403: 'Access denied', 429: 'Rate limit or quota exceeded'}
        return f"{labels.get(error.status_code, 'Provider request failed')} (HTTP {error.status_code})"
    if isinstance(error, (ProviderError, ValueError, OSError)):
        return str(error)[:300]
    return 'Request failed unexpectedly. Check the configuration and retry.'


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
        raise ProviderError('The selected model does not support chat')
    if images and (not (spec.images or spec.video) or len(images) > spec.max_images):
        raise ProviderError('The selected model does not support these image attachments')
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.extend({'role': message['role'], 'content': message['content']} for message in history)
    content = [{'type': 'text', 'text': prompt or 'Describe the attached image.'}] if images else prompt
    for encoded, mime in images:
        if mime and mime.startswith('image/') and spec.images:
            kind = 'image_url'
        elif mime and mime.startswith('video/') and spec.video:
            kind = 'video_url'
        else:
            raise ProviderError('This model does not support the attachment type')
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
            raise ProviderError('Provider returned no chat choices')
        message = response.choices[0].message
        reply, thinking = message.content or '', reasoning_text(message)
    if not reply and not thinking:
        raise ProviderError('Provider returned an empty response')
    return reply, thinking


def normalize_images(result):
    if hasattr(result, 'model_dump'):
        result = result.model_dump()
    if not isinstance(result, dict):
        raise ProviderError('Provider returned invalid image data')
    raw = result.get('images', result.get('data', []))
    images = []
    if not isinstance(raw, list):
        raise ProviderError('Provider returned invalid image data')
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
                raise ProviderError('Invalid image data URL') from error
            url = None
        if encoded:
            try:
                if len(encoded) > 48 * 1024 * 1024:
                    raise ProviderError('Generated image exceeds the size limit')
                binary = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError, binascii.Error) as error:
                raise ProviderError('Invalid Base64 image response') from error
            images.append({'bytes': binary})
        elif isinstance(url, str) and urlparse(url).scheme == 'https' and urlparse(url).hostname:
            images.append({'url': url})
    if not images:
        raise ProviderError('Provider returned no usable images')
    return images


def _build_registry(adapters):
    provider_apis, models, providers = {}, {}, {}
    for adapter in adapters:
        declared_models = getattr(adapter, 'MODELS', None)
        declared_providers = getattr(adapter, 'PROVIDERS', None)
        if not isinstance(declared_models, dict) or not isinstance(declared_providers, dict) or declared_models.keys() != declared_providers.keys():
            raise ProviderError(f'{adapter.__name__}: MODELS and PROVIDERS must declare the same providers')
        for provider, names in declared_providers.items():
            model_specs = declared_models[provider]
            if not isinstance(provider, str) or not provider.strip() or not isinstance(model_specs, dict) or not model_specs:
                raise ProviderError(f'{adapter.__name__}: each provider must have a name and at least one model')
            if not isinstance(names, (list, tuple)) or list(model_specs) != list(names):
                raise ProviderError(f'{adapter.__name__}: model list does not match MODELS for {provider}')
            if provider in provider_apis:
                raise ProviderError(f'Duplicate provider name: {provider}')
            provider_apis[provider] = adapter
            models[provider] = model_specs
            providers[provider] = list(names)
    if not providers:
        raise ProviderError('No providers with models were found')
    for adapter in adapters:
        aliases = getattr(adapter, 'ALIASES', {})
        if not isinstance(aliases, dict):
            raise ProviderError(f'{adapter.__name__}: ALIASES must be a mapping')
        for alias, target in aliases.items():
            if not isinstance(alias, str) or not alias.strip() or not isinstance(target, str) or target not in adapter.MODELS:
                raise ProviderError(f'{adapter.__name__}: alias must target a provider declared by the same adapter')
            if alias in provider_apis:
                raise ProviderError(f'Duplicate provider name or alias: {alias}')
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

__all__ = [
    "PROVIDERS",
    "DEFAULT_PROVIDER", "DEFAULT_MODEL", "SHORT_TITLE_PROVIDER", "SHORT_TITLE_MODEL",
    "MUSIC_MODEL", "DEFAULT_IMAGE_MODEL", "is_image_model", "is_music_model",
    "call_chat_api", "call_image_api", "call_music_api",
    "MODELS", "ModelCapabilities", "ProviderError", "capabilities", "canonical_provider", "chat_completion",
    "error_message", "get_client", "normalize_images", "provider_settings",
    "configure_runtime", "read_provider_settings", "request_timeout", "tls_verify",
]

def _default_model(kind):
    return next((model for model, spec in MODELS[DEFAULT_PROVIDER].items() if spec.kind == kind), None)


DEFAULT_PROVIDER = next(iter(PROVIDERS))
DEFAULT_MODEL = _default_model('chat') or PROVIDERS[DEFAULT_PROVIDER][0]
SHORT_TITLE_PROVIDER = DEFAULT_PROVIDER
SHORT_TITLE_MODEL = _default_model('chat')
MUSIC_MODEL = _default_model('music')
DEFAULT_IMAGE_MODEL = _default_model('image')

def is_image_model(model):
    return any(spec.kind == "image" for models in MODELS.values() for name, spec in models.items() if name == model)

def is_music_model(model):
    return any(spec.kind == "music" for models in MODELS.values() for name, spec in models.items() if name == model)

def _api(provider, kind):
    handler = getattr(_adapter(provider), f'call_{kind}_api', None)
    if handler is None:
        raise ProviderError(f'{kind.capitalize()} is not supported by {provider}')
    return handler


def call_chat_api(provider, model, history, prompt, b64_images, system_prompt, **options):
    return _api(provider, 'chat')(model, history, prompt, b64_images, system_prompt, **options)

def call_image_api(provider, prompt, model, aspect_ratio="16:9", n=1, prompt_optimizer=True, subject_reference=None):
    spec = capabilities(provider, model)
    if spec.kind != "image" or aspect_ratio not in spec.ratios:
        raise ProviderError(f"Unsupported image aspect ratio for {model}: {aspect_ratio}")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= spec.max_count:
        raise ProviderError(f"{model} supports 1 to {spec.max_count} images per request")
    if subject_reference and not spec.reference_images:
        raise ProviderError(f"{model} does not support reference images in this application")
    return _api(provider, 'image')(
        prompt=prompt, model=model, aspect_ratio=aspect_ratio, n=n,
        prompt_optimizer=prompt_optimizer, subject_reference=subject_reference,
    )

def call_music_api(provider, prompt, lyrics, model, sample_rate):
    if capabilities(provider, model).kind != "music":
        raise ProviderError(f"Music generation is not supported by {provider} / {model}")
    return _api(provider, 'music')(
        prompt=prompt, lyrics=lyrics, model=model, sample_rate=sample_rate,
    )
