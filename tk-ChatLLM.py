# -*- coding: utf-8 -*-
import sys

sys.dont_write_bytecode = True

import base64
import copy
import hashlib
import io
import json
import os
import queue
import re
import requests
import tempfile
import threading
import tkinter as tk
import warnings
import webbrowser
from PIL import Image, ImageOps, ImageTk
from collections import OrderedDict
from concurrent.futures import CancelledError as TaskCancelled
from dataclasses import dataclass, replace
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from providers import DEFAULT_MODEL, DEFAULT_PROVIDER, PROVIDERS, ProviderError, call_chat_api, call_image_api, call_music_api, canonical_provider, capabilities, configure_runtime, error_message, provider_settings, request_timeout, tls_verify
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urljoin, urlparse
from uuid import uuid4


CHATLLM_CONNECT_TIMEOUT = 10
CHATLLM_READ_TIMEOUT = 120
CHATLLM_AUTO_TITLE = True
MINIMAX_STREAM_MODE = 'auto'


def load_configuration():
    load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
    configure_runtime(CHATLLM_CONNECT_TIMEOUT, CHATLLM_READ_TIMEOUT, MINIMAX_STREAM_MODE)


class SessionStoreError(Exception):
    pass


class SessionStore:
    SCHEMA_VERSION = 2

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.issues = []

    def _path(self, session_id):
        if not isinstance(session_id, str) or not session_id or any(character in session_id for character in '/\\\x00:'):
            raise SessionStoreError('Invalid session ID')
        if session_id in {'.', '..'} or Path(session_id).name != session_id:
            raise SessionStoreError('Invalid session ID')
        return self.directory / f'{session_id}.json'

    @staticmethod
    def legacy_title(session_id):
        parts = session_id.split('-', 4)
        title = parts[4] if len(parts) == 5 else ''
        return title if title and not title.isdigit() else 'New conversation'

    def create(self, provider, model, system_prompt):
        timestamp = datetime.now().isoformat()
        return {
            'schema_version': self.SCHEMA_VERSION,
            'id': uuid4().hex,
            'title': '',
            'provider': provider,
            'model': model,
            'system_prompt': system_prompt,
            'messages': [],
            'created_at': timestamp,
            'updated_at': timestamp,
            'draft': '',
            'draft_attachments': [],
            'draft_lyrics': '',
        }

    def load(self, session_id):
        path = self._path(session_id)
        try:
            with path.open(encoding='utf-8-sig') as source:
                record = json.load(source)
            if not isinstance(record, dict) or not isinstance(record.get('messages'), list):
                raise ValueError('Expected a conversation object with a messages list')
            for field in ('title', 'provider', 'model', 'system_prompt', 'draft', 'draft_lyrics'):
                if field in record and not isinstance(record[field], str):
                    raise ValueError(f'Invalid conversation field: {field}')
            if not isinstance(record.get('draft_attachments', []), list) or not all(isinstance(path, str) for path in record.get('draft_attachments', [])):
                raise ValueError('Invalid draft attachments')
            last_user_id = None
            message_ids = set()
            for message in record['messages']:
                if not isinstance(message, dict) or message.get('role') not in {'user', 'assistant', 'system'}:
                    raise ValueError('Invalid message')
                if not isinstance(message.get('content', ''), str):
                    raise ValueError('Invalid message content')
                for field in ('type', 'thinking', 'prompt', 'effective_prompt', 'lyrics', 'model', 'provider', 'progress', 'audio_url', 'cache_path'):
                    if message.get(field) is not None and not isinstance(message[field], str):
                        raise ValueError(f'Invalid message field: {field}')
                if message.get('type') is None:
                    message['type'] = 'text'
                for field in ('images', 'attachments'):
                    items = message.get(field, [])
                    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                        raise ValueError(f'Invalid message field: {field}')
                for image in message.get('images', []):
                    if any(image.get(field) is not None and not isinstance(image[field], str) for field in ('url', 'cache_path')):
                        raise ValueError('Invalid image reference')
                for attachment in message.get('attachments', []):
                    if not all(isinstance(attachment.get(field), str) for field in ('name', 'path', 'mime')):
                        raise ValueError('Invalid attachment reference')
                if not isinstance(message.get('request', {}), dict):
                    raise ValueError('Invalid stored request')
                message_id = message.get('id')
                if not isinstance(message_id, str) or not message_id or message_id in message_ids:
                    message_id = uuid4().hex
                    message['id'] = message_id
                message_ids.add(message_id)
                if message['role'] == 'user':
                    last_user_id = message_id
                if message.get('type', '').endswith('_loading') or message.get('status') == 'pending':
                    partial = message.get('content', '')
                    message['type'] = 'error'
                    message['status'] = 'interrupted'
                    message['content'] = (partial + '\n' if partial else '') + 'The previous request was interrupted. You can retry it.'
                if message['role'] == 'assistant' and message.get('type') == 'error' and last_user_id:
                    message.setdefault('retry_user_id', last_user_id)
            record['id'] = session_id
            record['schema_version'] = self.SCHEMA_VERSION
            record.setdefault('title', self.legacy_title(session_id))
            record.setdefault('created_at', datetime.fromtimestamp(path.stat().st_mtime).isoformat())
            record.setdefault('updated_at', record['created_at'])
            record.setdefault('draft', '')
            record.setdefault('draft_attachments', [])
            record.setdefault('draft_lyrics', '')
            return record
        except (OSError, ValueError, TypeError) as error:
            raise SessionStoreError(f'Cannot read conversation {path.name}: {error}') from error

    def scan(self):
        self.issues = []
        records = []
        for path in self.directory.glob('*.json'):
            if path.name == 'index.json':
                continue
            try:
                records.append(self.load(path.stem))
            except SessionStoreError as error:
                self.issues.append(str(error))
        return sorted(records, key=lambda record: str(record['created_at']), reverse=True)

    def save(self, record):
        path = self._path(record['id'])
        snapshot = copy.deepcopy(record)
        snapshot['schema_version'] = self.SCHEMA_VERSION
        snapshot['updated_at'] = datetime.now().isoformat()
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.directory,
                prefix=f'.{path.stem}-', suffix='.tmp', delete=False,
            ) as target:
                temporary_path = Path(target.name)
                json.dump(snapshot, target, ensure_ascii=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
            record['updated_at'] = snapshot['updated_at']
        except (OSError, ValueError, TypeError) as error:
            raise SessionStoreError(f'Cannot save conversation {path.name}: {error}') from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def delete(self, session_id):
        try:
            self._path(session_id).unlink(missing_ok=True)
        except OSError as error:
            raise SessionStoreError(f'Cannot delete conversation: {error}') from error


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    kind: str
    payload: object = None


class TaskContext:
    def __init__(self, task_id, cancelled, events):
        self.task_id = task_id
        self.cancelled = cancelled
        self._events = events

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise TaskCancelled('Task cancelled')

    def emit(self, kind, payload):
        self.check_cancelled()
        self._events.put(TaskEvent(self.task_id, kind, payload))


class TaskRunner:
    def __init__(self, max_workers=4, max_pending=16):
        self._jobs = queue.Queue(maxsize=max_pending)
        self._events = queue.SimpleQueue()
        self._tasks = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._limit = max_pending
        self._threads = [
            threading.Thread(target=self._worker, name=f'chatllm-worker-{index}', daemon=True)
            for index in range(max_workers)
        ]
        for worker in self._threads:
            worker.start()

    def submit(self, work, *arguments, owner=None):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('Task runner is closed')
            if len(self._tasks) >= self._limit:
                raise RuntimeError('Too many pending tasks. Try again after a task completes.')
            task_id = uuid4().hex
            context = TaskContext(task_id, threading.Event(), self._events)
            self._tasks[task_id] = (context, owner)
            self._jobs.put_nowait((context, work, arguments))
            return task_id

    def cancel(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task[0].cancelled.set()

    def cancel_owner(self, owner):
        with self._lock:
            for context, task_owner in self._tasks.values():
                if task_owner == owner:
                    context.cancelled.set()

    def drain(self, limit=200):
        events = []
        for _ in range(limit):
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

    def shutdown(self, wait=False):
        self._closed.set()
        with self._lock:
            for context, owner in self._tasks.values():
                context.cancelled.set()
        if wait:
            for worker in self._threads:
                worker.join()

    def _worker(self):
        while not self._closed.is_set() or not self._jobs.empty():
            try:
                context, work, arguments = self._jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                context.check_cancelled()
                result = work(context, *arguments)
                context.check_cancelled()
                self._events.put(TaskEvent(context.task_id, 'result', result))
            except TaskCancelled:
                self._events.put(TaskEvent(context.task_id, 'cancelled'))
            except Exception as error:
                self._events.put(TaskEvent(context.task_id, 'error', error))
            finally:
                with self._lock:
                    self._tasks.pop(context.task_id, None)
                self._jobs.task_done()


class MediaCache:
    def __init__(self, directory, max_bytes=64 * 1024 * 1024):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._locks = [threading.Lock() for _ in range(32)]
        self._local = threading.local()

    @staticmethod
    def validate_url(url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ProviderError('Media downloads require an HTTPS URL without credentials')
        return url

    @staticmethod
    def image_bytes(data):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    output = io.BytesIO()
                    ImageOps.exif_transpose(image).convert('RGB').save(output, format='PNG')
                    return output.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ProviderError('Invalid or excessively large image') from error

    @staticmethod
    def thumbnail(path, size=(350, 350)):
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail(size, Image.Resampling.LANCZOS)
                return image.copy()

    def _session(self):
        if not hasattr(self._local, 'session'):
            self._local.session = requests.Session()
        return self._local.session

    def _download(self, url, context=None):
        session = self._session()
        for _ in range(6):
            self.validate_url(url)
            if context is not None:
                context.check_cancelled()
            response = session.get(
                url, stream=True, timeout=request_timeout(), verify=tls_verify(),
                allow_redirects=False, headers={'User-Agent': 'ChatLLM/1.0'},
            )
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('Location')
                    if not location:
                        raise ProviderError('Media redirect has no destination')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise ProviderError(f'Media download failed (HTTP {response.status_code})')
                length = response.headers.get('Content-Length')
                if length and int(length) > self.max_bytes:
                    raise ProviderError('Media download exceeds the size limit')
                data = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if context is not None:
                        context.check_cancelled()
                    if len(data) + len(chunk) > self.max_bytes:
                        raise ProviderError('Media download exceeds the size limit')
                    data.extend(chunk)
                if not data:
                    raise ProviderError('Media download was empty')
                return bytes(data)
            finally:
                response.close()
        raise ProviderError('Too many media redirects')

    def _write_atomic(self, path, data):
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.media-', suffix='.tmp', delete=False) as target:
                temporary_path = Path(target.name)
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def store_bytes(self, data, kind='image'):
        if not data or len(data) > self.max_bytes:
            raise ProviderError('Media data is empty or exceeds the size limit')
        if kind == 'image':
            data = self.image_bytes(data)
        if len(data) > self.max_bytes:
            raise ProviderError('Decoded media exceeds the size limit')
        digest = hashlib.sha256(data).hexdigest()
        path = self.directory / f"{digest}{'.png' if kind == 'image' else '.mp3'}"
        with self._locks[int(digest[:2], 16) % len(self._locks)]:
            if not path.exists():
                self._write_atomic(path, data)
        return str(path)

    def fetch(self, url, kind='image', context=None):
        self.validate_url(url)
        digest = hashlib.sha256(url.encode('utf-8')).hexdigest()
        path = self.directory / f"{digest}{'.png' if kind == 'image' else '.mp3'}"
        with self._locks[int(digest[:2], 16) % len(self._locks)]:
            if path.exists() and 0 < path.stat().st_size <= self.max_bytes:
                if kind != 'image':
                    return str(path)
                try:
                    with Image.open(path) as image:
                        image.verify()
                    return str(path)
                except (OSError, ValueError):
                    pass
            data = self._download(url, context)
            if kind == 'image':
                data = self.image_bytes(data)
            if len(data) > self.max_bytes:
                raise ProviderError('Decoded media exceeds the size limit')
            if context is not None:
                context.check_cancelled()
            self._write_atomic(path, data)
        return str(path)

    def save_to(self, source_path, destination):
        source = Path(source_path)
        if not source.is_file() or source.stat().st_size > self.max_bytes:
            raise ProviderError('Cached media is unavailable or too large')
        destination = Path(destination)
        if source.resolve() != destination.resolve():
            self._write_atomic(destination, source.read_bytes())
        return str(destination)

    def prepare_image(self, item, context=None):
        if item.get('bytes'):
            path = self.store_bytes(item['bytes'])
        elif item.get('cache_path') and Path(item['cache_path']).is_file():
            path = item['cache_path']
        else:
            path = self.fetch(item.get('url', ''), context=context)
        return {'url': item.get('url', ''), 'cache_path': path, 'thumbnail': self.thumbnail(path)}


TEXT_EXTENSIONS = {'.txt', '.md', '.py', '.csv', '.json', '.xml', '.yaml', '.yml'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}
VIDEO_MIMES = {'.mp4': 'video/mp4', '.avi': 'video/x-msvideo', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska'}
MAX_TEXT_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILES = 8


class AttachmentStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_paths(paths, spec):
        if len(paths) > MAX_FILES:
            raise ProviderError(f'At most {MAX_FILES} attachments are allowed')
        total = 0
        images = 0
        for filename in paths:
            path = Path(filename)
            extension = path.suffix.lower()
            if not path.is_file():
                raise ProviderError(f'Attachment is unavailable: {path.name}')
            if extension in TEXT_EXTENSIONS and spec.kind == 'chat':
                limit = MAX_TEXT_BYTES
            elif extension in IMAGE_EXTENSIONS and (spec.images or spec.reference_images):
                limit = MAX_IMAGE_BYTES
                images += 1
            elif extension in VIDEO_MIMES and spec.video:
                limit = MAX_VIDEO_BYTES
                images += 1
            else:
                raise ProviderError(f'This model does not support attachment: {path.name}')
            size = path.stat().st_size
            if not 0 < size <= limit:
                raise ProviderError(f'Attachment is empty or too large: {path.name}')
            total += size
        image_limit = 1 if spec.kind == 'image' else spec.max_images
        if images > image_limit or total > MAX_TOTAL_BYTES:
            raise ProviderError('Attachment count or total size exceeds the model limit')

    def ingest(self, paths, spec, context=None):
        self.validate_paths(paths, spec)
        attachments = []
        total = 0
        for filename in paths:
            if context is not None:
                context.check_cancelled()
            path = Path(filename)
            is_text = path.suffix.lower() in TEXT_EXTENSIONS
            is_video = path.suffix.lower() in VIDEO_MIMES
            limit = MAX_TEXT_BYTES if is_text else (MAX_VIDEO_BYTES if is_video else MAX_IMAGE_BYTES)
            with path.open('rb') as source:
                data = source.read(limit + 1)
            total += len(data)
            if not data or len(data) > limit or total > MAX_TOTAL_BYTES:
                raise ProviderError(f'Attachment changed or exceeds the size limit: {path.name}')
            if is_text:
                try:
                    data.decode('utf-8-sig')
                except UnicodeDecodeError as error:
                    raise ProviderError(f'Text attachment must use UTF-8: {path.name}') from error
                extension, mime = '.txt', 'text/plain'
            elif is_video:
                extension, mime = path.suffix.lower(), VIDEO_MIMES[path.suffix.lower()]
            else:
                data = MediaCache.image_bytes(data)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ProviderError(f'Decoded image is too large: {path.name}')
                extension, mime = '.png', 'image/png'
            digest = hashlib.sha256(data).hexdigest()
            target_path = self.directory / f'{digest}{extension}'
            temporary = None
            try:
                if not target_path.exists():
                    with tempfile.NamedTemporaryFile(dir=self.directory, delete=False, suffix='.tmp') as target:
                        temporary = Path(target.name)
                        target.write(data)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(temporary, target_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            attachments.append({'name': path.name, 'path': target_path.name, 'mime': mime, 'size': len(data)})
        return attachments

    def read(self, attachment):
        path = (self.directory / attachment['path']).resolve()
        if path.parent != self.directory.resolve() or not path.is_file():
            raise ProviderError(f"Attachment snapshot is unavailable: {attachment.get('name', 'unknown')}")
        limit = MAX_TEXT_BYTES if attachment['mime'].startswith('text/') else (MAX_VIDEO_BYTES if attachment['mime'].startswith('video/') else MAX_IMAGE_BYTES)
        with path.open('rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ProviderError('Attachment snapshot exceeds the size limit')
        return data

    def content(self, message, spec):
        text = message.get('prompt', message.get('content', ''))
        images = []
        for attachment in message.get('attachments', []):
            if attachment['mime'].startswith('text/'):
                text += f"\n\n[Attachment: {attachment['name']}]\n{self.read(attachment).decode('utf-8-sig')}"
            elif (attachment['mime'].startswith('image/') and spec.images) or (attachment['mime'].startswith('video/') and spec.video):
                images.append((base64.b64encode(self.read(attachment)).decode('ascii'), attachment['mime']))
            else:
                text += f"\n[Image attachment not supported by current model: {attachment['name']}]"
        return text, images


def media_token_cost(mime):
    return 8192 if mime.startswith('video/') else 5120


def estimate_tokens(content):
    if isinstance(content, list):
        return sum(8192 if part.get('type') == 'video_url' else (5120 if part.get('type') == 'image_url' else len(part.get('text', '').encode('utf-8'))) for part in content) + 8
    return len(str(content).encode('utf-8')) + 8


def build_history(messages, store, spec, current_text, current_images, system_prompt):
    remaining = spec.context_budget - estimate_tokens(system_prompt) - estimate_tokens(current_text) - sum(media_token_cost(mime) for encoded, mime in current_images) - 2048
    if remaining < 0:
        raise ProviderError('The current prompt and attachments exceed the context budget. Use smaller attachments or a shorter prompt.')
    turns = []
    for message in messages:
        if message.get('type') in {'error', 'chat_loading', 'image_loading', 'music_loading'}:
            continue
        if message.get('role') == 'user':
            turns.append([message])
        elif message.get('role') == 'assistant' and turns:
            turns[-1].append(message)
    selected = []
    dropped = 0
    image_count = len(current_images)
    for turn in reversed(turns):
        api_turn = []
        turn_images = 0
        for message in turn:
            if message['role'] == 'user':
                text, images = store.content(message, spec)
                content = text
                if images:
                    content = [{'type': 'text', 'text': text}]
                    for encoded, mime in images:
                        kind = 'video_url' if mime.startswith('video/') else 'image_url'
                        content.append({'type': kind, kind: {'url': f'data:{mime};base64,{encoded}'}})
                    turn_images += len(images)
            elif message.get('type') in {'image', 'music'}:
                content = message.get('effective_prompt', message.get('prompt', ''))
                if message.get('lyrics'):
                    content += '\n' + message['lyrics']
            else:
                content = message.get('content', '')
            if content:
                api_turn.append({'role': message['role'], 'content': content})
        cost = sum(estimate_tokens(message['content']) for message in api_turn)
        if cost > remaining or image_count + turn_images > spec.max_images:
            dropped = len(turns) - len(selected)
            break
        selected.append(api_turn)
        remaining -= cost
        image_count += turn_images
    return [message for turn in reversed(selected) for message in turn], dropped


def parse_image_options(prompt, default_aspect='1:1', default_count=1):
    aspect, count = default_aspect, default_count
    explicit = re.search(r'(?<!\d)(\d{1,2})\s*[:\u6bd4]\s*(\d{1,2})(?!\d)', prompt)
    if explicit:
        aspect = f'{int(explicit.group(1))}:{int(explicit.group(2))}'
    else:
        hints = {
            '\u5934\u50cf': '1:1', '\u58c1\u7eb8': '16:9',
            '\u6a2a\u56fe': '16:9', '\u6a2a\u7248': '16:9',
            '\u7ad6\u56fe': '9:16', '\u7ad6\u7248': '9:16',
        }
        for hint, ratio in hints.items():
            if hint in prompt:
                aspect = ratio
                break
    numbers = {'\u4e00': 1, '\u4e24': 2, '\u4e8c': 2, '\u4e09': 3, '\u56db': 4, '\u4e94': 5, '\u516d': 6, '\u4e03': 7, '\u516b': 8, '\u4e5d': 9, '\u5341': 10}
    match = re.search(r'(?<!\d)(\d+|[\u4e00\u4e24\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341])\s*(?:\u5f20|\u5e45)', prompt)
    if match is None:
        match = re.search(r'(?:\u6570\u91cf|\u5f20\u6570)\s*[:\uff1a]?\s*(\d+|[\u4e00\u4e24\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341])', prompt)
    if match:
        token = match.group(1)
        count = int(token) if token.isdigit() else numbers[token]
    return aspect, count


@dataclass(frozen=True)
class GenerationRequest:
    session_id: str
    user_id: str
    assistant_id: str
    provider: str
    model: str
    prompt: str
    system_prompt: str
    history: tuple = ()
    attachment_paths: tuple = ()
    attachments: tuple = ()
    aspect_ratio: str = '1:1'
    count: int = 1
    lyrics: str = ''


class ConversationService:
    def __init__(self, attachment_store, media_cache):
        self.attachments = attachment_store
        self.media = media_cache

    @staticmethod
    def text_model(provider):
        return next((model for model in PROVIDERS.get(canonical_provider(provider), []) if capabilities(provider, model).kind == 'chat'), None)

    def run(self, context, request):
        spec = capabilities(request.provider, request.model)
        provider_settings(request.provider, native=spec.kind != 'chat' and request.provider.startswith('MiniMax'))
        context.check_cancelled()
        attachments = list(request.attachments)
        if request.attachment_paths:
            context.emit('progress', 'Preparing attachments')
            attachments = self.attachments.ingest(request.attachment_paths, spec, context)
        context.emit('prepared', {'attachments': attachments})
        current = {'role': 'user', 'prompt': request.prompt, 'attachments': attachments}
        if spec.kind == 'chat':
            prompt, images = self.attachments.content(current, spec)
            history, dropped = build_history(request.history, self.attachments, spec, prompt, images, request.system_prompt)
            context.emit('context', {'dropped_turns': dropped})
            context.emit('progress', 'Generating response')
            reply, thinking = call_chat_api(
                request.provider, request.model, history, prompt, images, request.system_prompt,
                on_delta=lambda text, reasoning: context.emit('delta', (text, reasoning)),
                cancel_event=context.cancelled,
            )
            return {'type': 'text', 'content': reply, 'thinking': thinking, 'dropped_turns': dropped}
        refined_prompt = self._refine_prompt(context, request, spec.kind)
        context.check_cancelled()
        if spec.kind == 'image':
            reference = None
            if attachments:
                image = attachments[0]
                encoded = base64.b64encode(self.attachments.read(image)).decode('ascii')
                reference = [{'type': 'character', 'image_file': f"data:{image['mime']};base64,{encoded}"}]
            context.emit('progress', 'Generating images')
            result = call_image_api(
                request.provider, refined_prompt, request.model, request.aspect_ratio,
                n=request.count, subject_reference=reference,
            )
            context.check_cancelled()
            images = []
            thumbnails = {}
            for item in result['images']:
                try:
                    prepared = self.media.prepare_image(item, context)
                    thumbnails[prepared['cache_path']] = prepared.pop('thumbnail')
                    images.append(prepared)
                except (ProviderError, OSError, requests.RequestException) as error:
                    if not item.get('url'):
                        raise
                    images.append({'url': item['url'], 'cache_error': error_message(error)})
            return {
                'type': 'image', 'prompt': request.prompt, 'effective_prompt': refined_prompt,
                'aspect_ratio': request.aspect_ratio, 'n': len(images), 'requested_n': request.count,
                'images': images, 'thumbnails': thumbnails,
            }
        lyrics = request.lyrics.strip()
        if not lyrics:
            text_model = self.text_model(request.provider)
            if text_model is None:
                raise ProviderError('A text model or custom lyrics is required to generate music')
            context.emit('progress', 'Writing lyrics')
            lyrics, _ = call_chat_api(
                request.provider, text_model, [], refined_prompt, [],
                'Write a short original Chinese song based on the user theme. Output only the lyrics, '
                'one line per phrase, 10 to 15 lines. Do not include a title, explanations, or section labels.',
                cancel_event=context.cancelled,
            )
            if not lyrics.strip():
                raise ProviderError('The lyrics response was empty. Add custom lyrics and retry.')
        context.check_cancelled()
        context.emit('progress', 'Generating music')
        result = call_music_api(request.provider, refined_prompt, lyrics, request.model, 44100)
        data = result.get('data') or {}
        status = data.get('status', 2)
        audio_url = data.get('audio', '')
        if status != 2 or not audio_url:
            raise ProviderError('Music generation did not return a completed audio file')
        context.check_cancelled()
        cache_path = None
        cache_error = ''
        try:
            cache_path = self.media.fetch(audio_url, kind='music', context=context)
        except (ProviderError, OSError, requests.RequestException) as error:
            cache_error = error_message(error)
        return {
            'type': 'music', 'prompt': request.prompt, 'effective_prompt': refined_prompt,
            'lyrics': data.get('lyrics') or lyrics, 'audio_url': audio_url,
            'cache_path': cache_path, 'cache_error': cache_error, 'status': status,
        }

    def _refine_prompt(self, context, request, kind):
        if not request.history:
            return request.prompt
        model = self.text_model(request.provider)
        if model is None:
            return request.prompt
        spec = replace(capabilities(request.provider, model), images=False, video=False)
        system_prompt = (
            f'Rewrite the latest request as a detailed {kind} generation prompt using the previous conversation. '
            'Preserve the requested changes and output only the final prompt. Do not add explanations.'
        )
        history, dropped = build_history(request.history, self.attachments, spec, request.prompt, [], system_prompt)
        context.emit('context', {'dropped_turns': dropped})
        context.emit('progress', 'Preparing generation prompt')
        try:
            prompt, _ = call_chat_api(
                request.provider, model, history, request.prompt, [], system_prompt,
                cancel_event=context.cancelled,
            )
            return prompt.strip() or request.prompt
        except ProviderError:
            return request.prompt

    def title(self, context, provider, prompt):
        model = self.text_model(provider)
        if model is None:
            return ''
        result, _ = call_chat_api(
            provider, model, [], prompt[:1000], [],
            'Write a concise Chinese conversation title of at most 16 characters. Output only the title.',
            cancel_event=context.cancelled,
        )
        return ' '.join(result.strip().strip('"\'').split())[:16]


class ChatRenderer:
    def __init__(self, text, open_media, save_media, load_thumbnail, retry_request):
        self.text = text
        self.open_media = open_media
        self.save_media = save_media
        self.load_thumbnail = load_thumbnail
        self.retry_request = retry_request
        self.session_id = None
        self._ids = []
        self._signatures = {}
        self._windows = {}
        self._images = {}
        self._thumbnails = OrderedDict()
        self._active_thumbnail_keys = set()
        self._pending = set()
        self._thumbnail_errors = {}
        self._configure_tags()

    def _configure_tags(self):
        self.text.tag_configure('user', foreground='#64748b', font=('Microsoft YaHei', 9), spacing1=14, spacing3=4, justify='right')
        self.text.tag_configure('user_body', foreground='#0f172a', background='#e0f2fe', spacing2=3, spacing3=10, lmargin1=60, lmargin2=60, rmargin=12)
        self.text.tag_configure('assistant', foreground='#64748b', font=('Microsoft YaHei', 9), spacing1=14, spacing3=4)
        self.text.tag_configure('assistant_body', foreground='#0f172a', background='#f1f5f9', spacing2=3, spacing3=10, lmargin1=12, lmargin2=12, rmargin=30)
        self.text.tag_configure('thinking', foreground='#71717a', font=('Microsoft YaHei', 9), spacing3=8)
        self.text.tag_configure('error', foreground='#b91c1c', spacing1=6, spacing3=8)
        self.text.tag_configure('info', foreground='#64748b', spacing3=6)
        self.text.tag_configure('system', foreground='#9a6700', spacing3=8)
        self.text.tag_raise(tk.SEL)

    @staticmethod
    def thumbnail_key(item):
        return item.get('cache_path') or item.get('url', '')

    def remember_thumbnail(self, key, image):
        if not key:
            return
        self._pending.discard(key)
        self._thumbnail_errors.pop(key, None)
        self._thumbnails[key] = image
        self._thumbnails.move_to_end(key)
        while len(self._thumbnails) > 64:
            removable = next((candidate for candidate in self._thumbnails if candidate not in self._active_thumbnail_keys), None)
            if removable is None:
                break
            self._thumbnails.pop(removable)

    def thumbnail_failed(self, key, error):
        self._pending.discard(key)
        self._thumbnail_errors[key] = error

    def reset(self, session_id=None):
        for message_id in list(self._windows):
            self._destroy_message_widgets(message_id)
        for message_id in self._ids:
            self.text.mark_unset(f'start_{message_id}', f'end_{message_id}')
        self.text.config(state='normal')
        self.text.delete('1.0', tk.END)
        self.text.config(state='disabled')
        self._ids.clear()
        self._signatures.clear()
        self._images.clear()
        self.session_id = session_id

    def _destroy_message_widgets(self, message_id):
        for widget in self._windows.pop(message_id, []):
            widget.destroy()
        self._images.pop(message_id, None)

    def sync(self, session_id, messages, force_ids=()):
        message_ids = [message['id'] for message in messages]
        at_bottom = self.text.yview()[1] >= 0.98
        reset = session_id != self.session_id or message_ids[:len(self._ids)] != self._ids
        if reset:
            self.reset(session_id)
            at_bottom = True
        self._active_thumbnail_keys = {
            self.thumbnail_key(item) for message in messages for item in message.get('images', [])
        }
        self.text.config(state='normal')
        try:
            for message_index, message in enumerate(messages):
                message_id = message['id']
                signature = json.dumps(message, sort_keys=True, ensure_ascii=False)
                if signature == self._signatures.get(message_id) and message_id not in force_ids:
                    continue
                start_mark, end_mark = f'start_{message_id}', f'end_{message_id}'
                next_start = None
                if message_id in self._signatures:
                    if message_index + 1 < len(self._ids):
                        next_start = f'start_{self._ids[message_index + 1]}'
                        self.text.mark_gravity(next_start, 'right')
                    self._destroy_message_widgets(message_id)
                    self.text.delete(start_mark, end_mark)
                    self.text.mark_set('insert_message', start_mark)
                else:
                    self.text.mark_set(start_mark, 'end-1c')
                    self.text.mark_gravity(start_mark, 'left')
                    self.text.mark_set('insert_message', 'end-1c')
                    self._ids.append(message_id)
                self.text.mark_gravity('insert_message', 'right')
                self._render_message(message)
                self.text.mark_set(end_mark, 'insert_message')
                self.text.mark_gravity(end_mark, 'left')
                if next_start is not None:
                    self.text.mark_gravity(next_start, 'left')
                self._signatures[message_id] = signature
        finally:
            self.text.config(state='disabled')
        if at_bottom:
            self.text.see(tk.END)

    def _insert(self, text, tag='assistant_body'):
        self.text.insert('insert_message', text, tag)

    def _embed(self, message_id, widget):
        self._windows.setdefault(message_id, []).append(widget)
        self.text.window_create('insert_message', window=widget)
        self._insert('\n', 'info')

    def _render_message(self, message):
        message_id = message['id']
        role = message.get('role')
        kind = message.get('type', 'text')
        if role == 'user':
            self._insert('\u7528\u6237\n', 'user')
            self._insert(message.get('content', '') + '\n', 'user_body')
            return
        if role == 'system':
            self._insert(message.get('content', '') + '\n', 'system')
            return
        self._insert((message.get('model') or '\u52a9\u624b') + '\n', 'assistant')
        if kind == 'error':
            self._insert(message.get('content', 'Request failed') + '\n', 'error')
            if message.get('retry_user_id'):
                button = ttk.Button(self.text, text='\u91cd\u8bd5', command=lambda user_id=message['retry_user_id']: self.retry_request(user_id))
                self._embed(message_id, button)
            return
        if message.get('thinking'):
            self._insert(message['thinking'] + '\n', 'thinking')
        if kind in {'text', 'chat_loading'}:
            self._insert(message.get('content') or ('\u6b63\u5728\u751f\u6210...' if message.get('status') == 'pending' else ''), 'assistant_body')
            self._insert('\n', 'assistant_body')
        elif kind.endswith('_loading'):
            self._insert(message.get('progress', '\u6b63\u5728\u751f\u6210...') + '\n', 'info')
        elif kind == 'image':
            self._insert(message.get('prompt', '') + '\n')
            self._insert(f"{message.get('aspect_ratio', '')} | {len(message.get('images', []))} \u5f20\n", 'info')
            for index, item in enumerate(message.get('images', [])):
                self._render_image(message_id, item, index)
        elif kind == 'music':
            self._insert(message.get('prompt', '') + '\n')
            self._insert(message.get('lyrics', '') + '\n')
            item = {'url': message.get('audio_url', ''), 'cache_path': message.get('cache_path'), 'kind': 'music'}
            self._media_buttons(message_id, item, 'music.mp3')
        if message.get('cache_error'):
            self._insert(message['cache_error'] + '\n', 'error')
        if message.get('dropped_turns'):
            self._insert(f"\u4e0a\u4e0b\u6587\u5df2\u7701\u7565 {message['dropped_turns']} \u8f6e\u8f83\u65e9\u5bf9\u8bdd\n", 'info')

    def _render_image(self, message_id, item, index):
        key = self.thumbnail_key(item)
        thumbnail = self._thumbnails.get(key)
        if thumbnail is not None:
            image = ImageTk.PhotoImage(thumbnail, master=self.text)
            self._images.setdefault(message_id, []).append(image)
            self.text.image_create('insert_message', image=image)
            self._insert('\n', 'info')
        elif key in self._thumbnail_errors:
            self._insert(self._thumbnail_errors[key] + '\n', 'error')
        elif key:
            self._insert('\u6b63\u5728\u52a0\u8f7d\u56fe\u7247...\n', 'info')
            if key not in self._pending:
                self._pending.add(key)
                self.load_thumbnail(self.session_id, message_id, dict(item), key)
        self._media_buttons(message_id, dict(item, kind='image'), f'image-{index + 1}.png')

    def _media_buttons(self, message_id, item, filename):
        frame = ttk.Frame(self.text)
        ttk.Button(frame, text='\u6253\u5f00', command=lambda: self.open_media(item)).pack(side='left', padx=3)
        ttk.Button(frame, text='\u4fdd\u5b58', command=lambda: self.save_media(item, filename)).pack(side='left', padx=3)
        self._embed(message_id, frame)


DEFAULT_SYSTEM_PROMPT = '\u4f60\u662f\u667a\u80fd\u52a9\u624b\uff0c\u59cb\u7ec8\u7528\u4e2d\u6587\u56de\u590d\u3002'
FONT_UI = 'Microsoft YaHei'
NEW_SESSION = '\u65b0\u4f1a\u8bdd'


class ChatLLM_GUI(tk.Tk):
    def __init__(self, data_dir=None, frameless=True):
        super().__init__()
        self.withdraw()
        self.title('ChatLLM - \u667a\u80fd\u52a9\u624b')
        self.minsize(800, 600)
        width = max(800, min(1400, self.winfo_screenwidth() * 2 // 3))
        height = max(600, min(950, self.winfo_screenheight() * 2 // 3))
        self.geometry(f'{width}x{height}+{max(0, (self.winfo_screenwidth() - width) // 2)}+{max(0, (self.winfo_screenheight() - height) // 2)}')
        self._frameless = frameless and sys.platform == 'win32'
        if self._frameless:
            self.overrideredirect(True)
        directory = Path(data_dir) if data_dir is not None else Path(__file__).resolve().parent / 'conversations'
        self.session_store = SessionStore(directory)
        self.media_cache = MediaCache(directory / 'cache')
        self.attachment_store = AttachmentStore(directory / 'attachments')
        self.service = ConversationService(self.attachment_store, self.media_cache)
        self.runner = TaskRunner()
        self._session_records = {}
        self.current_session_id = None
        self.current_messages = []
        self.sessions = []
        self.attached_files = []
        self.custom_lyrics = ''
        self._tasks = {}
        self._active_requests = {}
        self._closing = False
        self._loading_flag = False
        self._is_processing = False
        self.sidebar_visible = True
        self._dirty_sessions = set()
        self._poll_id = None
        self._save_id = None
        self._maximized = False
        self.style = ttk.Style(self)
        if 'vista' in self.style.theme_names():
            self.style.theme_use('vista')
        self.style.configure('TFrame', background='#f8fafc')
        self.style.configure('TLabel', background='#f8fafc', font=(FONT_UI, 10), foreground='#0f172a')
        self.style.configure('TButton', font=(FONT_UI, 9))
        self.style.configure('TLabelframe', background='#f8fafc')
        self.style.configure('TLabelframe.Label', font=(FONT_UI, 9), foreground='#475569')
        self.setup_ui()
        self.renderer = ChatRenderer(self.chat_display, self._open_media, self._save_media, self._load_thumbnail, self.retry_message)
        self.load_all_sessions()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._poll_id = self.after(50, self._poll_tasks)
        self._save_id = self.after(10000, self._autosave)
        self.deiconify()
        if self._frameless:
            self.after(100, self._fix_taskbar)

    def setup_ui(self):
        if self._frameless:
            self._setup_titlebar()
        self.main_paned = tk.PanedWindow(self, orient='horizontal', sashwidth=5, bd=0, bg='#e2e8f0')
        self.main_paned.pack(fill='both', expand=True)
        self.sidebar_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(self.sidebar_frame, width=260, minsize=45)
        self.btn_toggle_sidebar = ttk.Button(self.sidebar_frame, text='\u25c0 \u6536\u8d77\u4fa7\u8fb9\u680f', command=self.toggle_sidebar)
        self.btn_toggle_sidebar.pack(fill='x', padx=5, pady=5)
        self.sidebar_paned = ttk.PanedWindow(self.sidebar_frame, orient='vertical')
        self.sidebar_paned.pack(fill='both', expand=True, padx=5, pady=5)
        history_frame = ttk.LabelFrame(self.sidebar_paned, text=' \u4f1a\u8bdd\u5386\u53f2 ', padding=5)
        self.sidebar_paned.add(history_frame, weight=3)
        actions = ttk.Frame(history_frame)
        actions.pack(fill='x', pady=(0, 6))
        self.btn_new_chat = ttk.Button(actions, text='\u65b0\u5efa\u4f1a\u8bdd', command=self.new_session)
        self.btn_new_chat.pack(side='left', fill='x', expand=True)
        self.btn_delete_chat = ttk.Button(actions, text='\u5220\u9664', command=self.delete_session, width=6)
        self.btn_delete_chat.pack(side='right', padx=(5, 0))
        list_frame = ttk.Frame(history_frame)
        list_frame.pack(fill='both', expand=True)
        self.history_listbox = tk.Listbox(list_frame, exportselection=False, activestyle='none', font=(FONT_UI, 10), bd=0, bg='#fbfbfb', selectbackground='#3b82f6', selectforeground='#ffffff')
        self.history_listbox.pack(side='left', fill='both', expand=True)
        list_scroll = ttk.Scrollbar(list_frame, command=self.history_listbox.yview)
        list_scroll.pack(side='right', fill='y')
        self.history_listbox.config(yscrollcommand=list_scroll.set)
        self.history_listbox.bind('<<ListboxSelect>>', self.on_session_select)
        settings = ttk.LabelFrame(self.sidebar_paned, text=' \u9009\u62e9\u6a21\u578b ', padding=8)
        self.sidebar_paned.add(settings, weight=2)
        ttk.Label(settings, text='\u63d0\u4f9b\u5546:').pack(anchor='w')
        self.provider_combo = ttk.Combobox(settings, state='readonly', values=list(PROVIDERS))
        self.provider_combo.pack(fill='x', pady=(2, 8))
        self.provider_combo.set(DEFAULT_PROVIDER)
        self.provider_combo.bind('<<ComboboxSelected>>', self.update_model_options)
        ttk.Label(settings, text='\u6a21\u578b\u540d\u79f0:').pack(anchor='w')
        self.model_combo = ttk.Combobox(settings, state='readonly', values=PROVIDERS[DEFAULT_PROVIDER])
        self.model_combo.pack(fill='x', pady=(2, 8))
        self.model_combo.set(DEFAULT_MODEL)
        self.model_combo.bind('<<ComboboxSelected>>', self.on_model_changed)
        ttk.Label(settings, text='\u7cfb\u7edf\u63d0\u793a\u8bcd:').pack(anchor='w')
        self.system_text = tk.Text(settings, height=4, wrap='word', font=(FONT_UI, 9), bd=1, relief='solid')
        self.system_text.pack(fill='both', expand=True, pady=(2, 0))
        self.system_text.insert('1.0', DEFAULT_SYSTEM_PROMPT)
        right = ttk.Frame(self.main_paned)
        self.main_paned.add(right, minsize=420)
        self.lbl_session_title = ttk.Label(right, text=NEW_SESSION, font=(FONT_UI, 10, 'bold'), padding=(10, 8))
        self.lbl_session_title.pack(fill='x')
        self.lbl_session_title.bind('<Configure>', lambda event: self.lbl_session_title.config(wraplength=max(100, event.width - 20)))
        self.status_bar = ttk.Label(right, text='\u51c6\u5907\u5c31\u7eea', padding=(8, 4), font=(FONT_UI, 9))
        self.status_bar.pack(side='bottom', fill='x')
        self.status_bar.bind('<Configure>', lambda event: self.status_bar.config(wraplength=max(100, event.width - 16)))
        chat_paned = ttk.PanedWindow(right, orient='vertical')
        chat_paned.pack(fill='both', expand=True, padx=5, pady=5)
        display_frame = ttk.Frame(chat_paned)
        chat_paned.add(display_frame, weight=4)
        self.chat_display = tk.Text(display_frame, wrap='word', state='disabled', bd=1, relief='solid', highlightthickness=0, bg='#ffffff', font=(FONT_UI, 10), padx=10, pady=6, cursor='xterm')
        self.chat_display.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(display_frame, command=self.chat_display.yview)
        scroll.pack(side='right', fill='y', before=self.chat_display)
        self.chat_display.config(yscrollcommand=scroll.set)
        self.chat_display.bind('<Control-c>', self._copy_selection)
        self.chat_display.bind('<Control-C>', self._copy_selection)
        input_frame = ttk.Frame(chat_paned)
        chat_paned.add(input_frame, weight=1)
        self.params_bar = ttk.Frame(input_frame)
        self.params_bar.pack(fill='x', pady=(5, 3))
        self.lbl_aspect = ttk.Label(self.params_bar, text='\u6bd4\u4f8b:')
        self.img_aspect_combo = ttk.Combobox(self.params_bar, state='readonly', width=7)
        self.lbl_n = ttk.Label(self.params_bar, text='\u5f20\u6570:')
        self.img_n_combo = ttk.Combobox(self.params_bar, state='readonly', width=3)
        self.btn_edit_lyrics = ttk.Button(self.params_bar, text='\u6dfb\u52a0\u6b4c\u8bcd', command=self.edit_lyrics_popup)
        attachment_bar = ttk.Frame(input_frame)
        attachment_bar.pack(fill='x', pady=(0, 3))
        self.btn_add_file = ttk.Button(attachment_bar, text='\u6dfb\u52a0\u9644\u4ef6', command=self.add_file)
        self.btn_add_file.pack(side='left')
        self.btn_clear_attachments = ttk.Button(attachment_bar, text='\u6e05\u9664', command=self.clear_attachments, width=5)
        self.btn_clear_attachments.pack(side='left', padx=4)
        self.lbl_attachments = ttk.Label(attachment_bar, text='', font=(FONT_UI, 9))
        self.lbl_attachments.pack(side='left', fill='x', expand=True)
        self.lbl_attachments.bind('<Configure>', lambda event: self.lbl_attachments.config(wraplength=max(60, event.width)))
        self.input_text = tk.Text(input_frame, height=4, wrap='word', font=(FONT_UI, 10), bd=1, relief='solid', padx=5, pady=5)
        self.input_text.pack(fill='both', expand=True)
        self.input_text.bind('<Return>', self.on_enter_pressed)
        self.input_text.bind('<Shift-Return>', self.on_shift_enter_pressed)
        command_bar = ttk.Frame(input_frame)
        command_bar.pack(side='bottom', fill='x', pady=4, before=self.input_text)
        self.btn_send = ttk.Button(command_bar, text='\u53d1\u9001', command=self.send_message)
        self.btn_send.pack(side='right')
        self.btn_cancel = ttk.Button(command_bar, text='\u53d6\u6d88\u8bf7\u6c42', command=self.cancel_request, state='disabled')
        self.btn_cancel.pack(side='right', padx=5)
        if self._frameless:
            ttk.Sizegrip(command_bar).pack(side='left')
        self.on_model_changed()

    def _setup_titlebar(self):
        bar = tk.Frame(self, bg='#e2e8f0', height=34)
        bar.pack(fill='x')
        bar.pack_propagate(False)
        title = tk.Label(bar, text='ChatLLM - \u667a\u80fd\u52a9\u624b', bg='#e2e8f0', fg='#1e293b', font=(FONT_UI, 10, 'bold'))
        title.pack(side='left', padx=10)
        for label, command in [('X', self._on_close), ('\u25a1', self._toggle_maximize), ('\u2212', self._minimize)]:
            tk.Button(bar, text=label, command=command, width=4, bd=0, bg='#e2e8f0', activebackground='#cbd5e1').pack(side='right', fill='y')
        for widget in (bar, title):
            widget.bind('<Button-1>', self._start_drag)
            widget.bind('<B1-Motion>', self._drag_window)
            widget.bind('<Double-Button-1>', lambda event: self._toggle_maximize())

    def _start_drag(self, event):
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_window(self, event):
        if not self._maximized:
            offset_x, offset_y = self._drag_offset
            self.geometry(f'+{event.x_root - offset_x}+{event.y_root - offset_y}')

    def _toggle_maximize(self):
        self._maximized = not self._maximized
        if self._maximized:
            self._normal_geometry = self.geometry()
            self.state('zoomed')
        else:
            self.state('normal')
            self.geometry(self._normal_geometry)

    def _minimize(self):
        self.overrideredirect(False)
        self.state('iconic')

        def restore(event):
            if event.widget is self and self.state() != 'iconic':
                self.overrideredirect(True)
                self.unbind('<Map>', binding)
                self._fix_taskbar()

        binding = self.bind('<Map>', restore, add='+')

    def _fix_taskbar(self):
        if self._closing or not self._frameless:
            return
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        handle = user32.GetParent(self.winfo_id()) or self.winfo_id()
        style = user32.GetWindowLongW(handle, -20)
        user32.SetWindowLongW(handle, -20, (style & ~0x80) | 0x40000)

    def toggle_sidebar(self):
        if self.sidebar_visible:
            self._sidebar_width = self.sidebar_frame.winfo_width()
            self.sidebar_paned.pack_forget()
            self.main_paned.paneconfigure(self.sidebar_frame, width=45)
            self.btn_toggle_sidebar.config(text='\u25b6')
        else:
            self.sidebar_paned.pack(fill='both', expand=True, padx=5, pady=5)
            self.main_paned.paneconfigure(self.sidebar_frame, width=getattr(self, '_sidebar_width', 260))
            self.btn_toggle_sidebar.config(text='\u25c0 \u6536\u8d77\u4fa7\u8fb9\u680f')
        self.sidebar_visible = not self.sidebar_visible

    def _copy_selection(self, event=None):
        try:
            selected = self.chat_display.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(selected)
        except tk.TclError:
            pass
        return 'break'

    def update_status(self, text):
        if not self._closing:
            self.status_bar.config(text=text)

    def _session_title_from_id(self, session_id):
        title = self._session_records.get(session_id, {}).get('title')
        return title if title and title != 'New conversation' else NEW_SESSION

    def load_all_sessions(self):
        self._loading_flag = True
        records = self.session_store.scan()
        self._session_records = {record['id']: record for record in records}
        self.sessions = [record['id'] for record in records]
        self.new_session()
        self.refresh_listbox_titles()
        self._loading_flag = False
        if self.session_store.issues:
            self.update_status(f'{len(self.session_store.issues)} \u4e2a\u5386\u53f2\u6587\u4ef6\u65e0\u6cd5\u8bfb\u53d6\uff0c\u539f\u6587\u4ef6\u5df2\u4fdd\u7559\u3002')

    def _capture_current(self):
        record = self._session_records.get(self.current_session_id)
        if record is not None:
            record.update(
                provider=self.provider_combo.get(), model=self.model_combo.get(),
                system_prompt=self.system_text.get('1.0', tk.END).strip(),
                draft=self.input_text.get('1.0', 'end-1c'),
                draft_attachments=list(self.attached_files), draft_lyrics=self.custom_lyrics,
            )
        return record

    @staticmethod
    def _has_content(record):
        return bool(record['messages'] or record.get('draft') or record.get('draft_attachments') or record.get('draft_lyrics'))

    def _persist_record(self, session_id, notify=True):
        record = self._session_records.get(session_id)
        if record is None or not self._has_content(record):
            return True
        self._dirty_sessions.add(session_id)
        try:
            self.session_store.save(record)
            self._dirty_sessions.discard(session_id)
            if session_id not in self.sessions:
                self.sessions.insert(0, session_id)
                self.refresh_listbox_titles()
            return True
        except SessionStoreError as error:
            self.update_status('\u4f1a\u8bdd\u4fdd\u5b58\u5931\u8d25\uff0c\u5185\u5bb9\u4ecd\u4fdd\u7559\u5728\u5185\u5b58\u4e2d\u3002')
            if notify:
                messagebox.showerror('\u4fdd\u5b58\u5931\u8d25', str(error), parent=self)
            return False

    def save_session_by_id(self, session_id):
        if session_id == self.current_session_id:
            self._capture_current()
        return self._persist_record(session_id)

    def _autosave(self):
        if self._closing:
            return
        record = self._capture_current()
        if record is not None and self._has_content(record):
            self._persist_record(record['id'], notify=False)
        for session_id in set(self._active_requests) | set(self._dirty_sessions):
            self._persist_record(session_id, notify=False)
        self._save_id = self.after(10000, self._autosave)

    def refresh_listbox_titles(self):
        self.history_listbox.delete(0, tk.END)
        for session_id in self.sessions:
            self.history_listbox.insert(tk.END, self._session_title_from_id(session_id))
        if self.current_session_id in self.sessions:
            self.history_listbox.selection_set(self.sessions.index(self.current_session_id))

    def new_session(self):
        if self.current_session_id and not self.save_session_by_id(self.current_session_id):
            return
        previous = self._session_records.get(self.current_session_id)
        if previous is not None and not self._has_content(previous):
            self._session_records.pop(previous['id'], None)
        record = self.session_store.create(self.provider_combo.get(), self.model_combo.get(), self.system_text.get('1.0', tk.END).strip())
        self._session_records[record['id']] = record
        self.load_session_by_id(record['id'])

    def load_session_by_id(self, session_id):
        record = self._session_records.get(session_id)
        if record is None:
            try:
                record = self.session_store.load(session_id)
            except SessionStoreError as error:
                messagebox.showerror('\u8bfb\u53d6\u5931\u8d25', str(error), parent=self)
                return
            self._session_records[session_id] = record
        self.current_session_id = session_id
        self.current_messages = record['messages']
        provider = canonical_provider(record.get('provider', DEFAULT_PROVIDER))
        if provider not in PROVIDERS:
            provider = DEFAULT_PROVIDER
        self.provider_combo.set(provider)
        self.model_combo['values'] = PROVIDERS[provider]
        model = record.get('model')
        self.model_combo.set(model if model in PROVIDERS[provider] else PROVIDERS[provider][0])
        self.system_text.delete('1.0', tk.END)
        self.system_text.insert('1.0', record.get('system_prompt', DEFAULT_SYSTEM_PROMPT))
        self.input_text.delete('1.0', tk.END)
        self.input_text.insert('1.0', record.get('draft', ''))
        self.attached_files = list(record.get('draft_attachments', []))
        self.custom_lyrics = record.get('draft_lyrics', '')
        self.on_model_changed()
        for message in reversed(self.current_messages):
            if message.get('type') == 'image':
                spec = capabilities(provider, self.model_combo.get())
                if message.get('aspect_ratio') in spec.ratios:
                    self.img_aspect_combo.set(message['aspect_ratio'])
                break
        self.lbl_session_title.config(text=self._session_title_from_id(session_id))
        self.refresh_listbox_titles()
        self.refresh_chat_display()
        self._sync_controls()

    def on_session_select(self, event=None):
        if self._loading_flag:
            return
        selected = self.history_listbox.curselection()
        if not selected or selected[0] >= len(self.sessions):
            return
        session_id = self.sessions[selected[0]]
        if session_id != self.current_session_id and self.save_session_by_id(self.current_session_id):
            self.load_session_by_id(session_id)

    def delete_session(self):
        selected = self.history_listbox.curselection()
        if not selected or selected[0] >= len(self.sessions):
            return
        session_id = self.sessions[selected[0]]
        if not messagebox.askyesno('\u5220\u9664\u4f1a\u8bdd', f'\u786e\u5b9a\u5220\u9664 {self._session_title_from_id(session_id)}\uff1f', parent=self):
            return
        try:
            self.session_store.delete(session_id)
        except SessionStoreError as error:
            messagebox.showerror('\u5220\u9664\u5931\u8d25', str(error), parent=self)
            return
        self.runner.cancel_owner(session_id)
        self._active_requests.pop(session_id, None)
        self._tasks = {task_id: task for task_id, task in self._tasks.items() if task['session_id'] != session_id}
        self._session_records.pop(session_id, None)
        self._dirty_sessions.discard(session_id)
        self.sessions.remove(session_id)
        if self.current_session_id == session_id:
            self.current_session_id = None
            if self.sessions:
                self.load_session_by_id(self.sessions[0])
            else:
                self.new_session()
        self.refresh_listbox_titles()

    def update_model_options(self, event=None):
        models = PROVIDERS[self.provider_combo.get()]
        self.model_combo['values'] = models
        self.model_combo.set(models[0])
        self.on_model_changed()

    def on_model_changed(self, event=None):
        spec = capabilities(self.provider_combo.get(), self.model_combo.get())
        for widget in (self.lbl_aspect, self.img_aspect_combo, self.lbl_n, self.img_n_combo, self.btn_edit_lyrics):
            widget.pack_forget()
        if spec.kind == 'image':
            for widget in (self.lbl_aspect, self.img_aspect_combo, self.lbl_n, self.img_n_combo):
                widget.pack(side='left', padx=(0, 5))
            self.img_aspect_combo['values'] = spec.ratios
            if self.img_aspect_combo.get() not in spec.ratios:
                self.img_aspect_combo.set('16:9' if '16:9' in spec.ratios else spec.ratios[0])
            counts = [str(count) for count in range(1, spec.max_count + 1)]
            self.img_n_combo['values'] = counts
            if self.img_n_combo.get() not in counts:
                self.img_n_combo.set('1')
        elif spec.kind == 'music':
            self.btn_edit_lyrics.pack(side='left')
        self.btn_edit_lyrics.config(text='\u6b4c\u8bcd (\u5df2\u7f16\u8f91)' if self.custom_lyrics else '\u6dfb\u52a0\u6b4c\u8bcd')
        self.update_attachments_ui()

    def add_file(self):
        paths = filedialog.askopenfilenames(parent=self, title='\u6dfb\u52a0\u9644\u4ef6', filetypes=[('Supported files', '*.txt *.md *.py *.csv *.json *.xml *.yaml *.yml *.png *.jpg *.jpeg *.gif *.bmp *.webp *.mp4 *.avi *.mov *.mkv'), ('All files', '*.*')])
        combined = list(dict.fromkeys(self.attached_files + list(paths)))
        try:
            self.attachment_store.validate_paths(combined, capabilities(self.provider_combo.get(), self.model_combo.get()))
        except (ProviderError, OSError) as error:
            messagebox.showerror('\u9644\u4ef6\u4e0d\u53ef\u7528', error_message(error), parent=self)
            return
        self.attached_files = combined
        self.update_attachments_ui()

    def clear_attachments(self):
        self.attached_files = []
        self.update_attachments_ui()

    def update_attachments_ui(self):
        self.lbl_attachments.config(text=', '.join(Path(path).name for path in self.attached_files) or '\u672a\u9009\u62e9\u9644\u4ef6')
        self.btn_clear_attachments.config(state='normal' if self.attached_files else 'disabled')
        spec = capabilities(self.provider_combo.get(), self.model_combo.get())
        self.btn_add_file.config(state='normal' if spec.kind == 'chat' or spec.reference_images else 'disabled')

    def edit_lyrics_popup(self):
        popup = tk.Toplevel(self)
        popup.title('\u7f16\u8f91\u6b4c\u8bcd')
        popup.geometry('450x350')
        popup.transient(self)
        popup.grab_set()
        editor = tk.Text(popup, wrap='word', font=(FONT_UI, 10))
        editor.pack(fill='both', expand=True, padx=10, pady=10)
        editor.insert('1.0', self.custom_lyrics)

        def save():
            self.custom_lyrics = editor.get('1.0', tk.END).strip()
            self.on_model_changed()
            popup.destroy()

        ttk.Button(popup, text='\u786e\u5b9a', command=save).pack(side='right', padx=10, pady=(0, 10))
        ttk.Button(popup, text='\u6e05\u7a7a', command=lambda: editor.delete('1.0', tk.END)).pack(side='right', padx=5, pady=(0, 10))

    def on_enter_pressed(self, event=None):
        self.send_message()
        return 'break'

    def on_shift_enter_pressed(self, event=None):
        self.input_text.insert(tk.INSERT, '\n')
        return 'break'

    @staticmethod
    def parse_image_prompt_overrides(prompt, default_aspect='1:1', default_n=1):
        return parse_image_options(prompt, default_aspect, default_n)

    def send_message(self):
        if self.current_session_id in self._active_requests:
            return
        prompt = self.input_text.get('1.0', tk.END).strip()
        if not prompt and not self.attached_files:
            return
        provider, model = self.provider_combo.get(), self.model_combo.get()
        try:
            spec = capabilities(provider, model)
            provider_settings(provider, native=spec.kind != 'chat' and provider.startswith('MiniMax'))
            self.attachment_store.validate_paths(self.attached_files, spec)
            if spec.kind != 'chat' and not prompt:
                raise ProviderError('A generation prompt is required')
            aspect, count = '1:1', 1
            if spec.kind == 'image':
                aspect, count = parse_image_options(prompt, self.img_aspect_combo.get(), int(self.img_n_combo.get()))
                if aspect not in spec.ratios or not 1 <= count <= spec.max_count:
                    raise ProviderError(f'Unsupported image settings. Ratios: {", ".join(spec.ratios)}; count: 1-{spec.max_count}')
                self.img_aspect_combo.set(aspect)
                self.img_n_combo.set(str(count))
        except (ProviderError, OSError, ValueError) as error:
            messagebox.showerror('\u65e0\u6cd5\u53d1\u9001', error_message(error), parent=self)
            return
        record = self._capture_current()
        user_id, assistant_id = uuid4().hex, uuid4().hex
        request = GenerationRequest(
            record['id'], user_id, assistant_id, provider, model, prompt,
            self.system_text.get('1.0', tk.END).strip(),
            history=tuple(copy.deepcopy(record['messages'])), attachment_paths=tuple(self.attached_files),
            aspect_ratio=aspect, count=count, lyrics=self.custom_lyrics,
        )
        content = prompt
        if self.attached_files:
            content += '\n[\u9644\u4ef6: ' + ', '.join(Path(path).name for path in self.attached_files) + ']'
        user = {
            'id': user_id, 'role': 'user', 'content': content, 'prompt': prompt,
            'attachment_paths': list(self.attached_files), 'attachments': [],
            'request': {'provider': provider, 'model': model, 'system_prompt': request.system_prompt, 'aspect_ratio': aspect, 'count': count, 'lyrics': self.custom_lyrics},
            'timestamp': datetime.now().isoformat(),
        }
        record['messages'].append(user)
        if not record.get('title'):
            record['title'] = ' '.join(prompt.split())[:16] or NEW_SESSION
        self.input_text.delete('1.0', tk.END)
        self.attached_files = []
        self.custom_lyrics = ''
        self._capture_current()
        self.on_model_changed()
        self._start_request(request)

    def _start_request(self, request):
        record = self._session_records[request.session_id]
        kind = capabilities(request.provider, request.model).kind
        message = {
            'id': request.assistant_id, 'role': 'assistant', 'type': kind + '_loading',
            'status': 'pending', 'content': '', 'thinking': '', 'model': request.model,
            'provider': request.provider, 'retry_user_id': request.user_id,
            'prompt': request.prompt, 'timestamp': datetime.now().isoformat(),
        }
        user_index = next(index for index, candidate in enumerate(record['messages']) if candidate['id'] == request.user_id)
        insertion = user_index + 1
        while insertion < len(record['messages']) and record['messages'][insertion].get('role') != 'user':
            insertion += 1
        record['messages'].insert(insertion, message)
        if not self._persist_record(record['id']):
            message.update(type='error', status='failed', content='Request was not sent because the conversation could not be saved.')
            self.refresh_chat_display()
            return
        try:
            task_id = self.runner.submit(self.service.run, request, owner=request.session_id)
        except RuntimeError as error:
            message.update(type='error', status='failed', content=str(error))
            self._persist_record(record['id'])
        else:
            self._tasks[task_id] = {'kind': 'request', 'session_id': request.session_id, 'request': request}
            self._active_requests[request.session_id] = task_id
        self.refresh_listbox_titles()
        self.lbl_session_title.config(text=self._session_title_from_id(self.current_session_id))
        self.refresh_chat_display()
        self._sync_controls()

    def retry_message(self, user_id):
        if self.current_session_id in self._active_requests:
            return
        record = self._session_records[self.current_session_id]
        user_index = next((index for index, message in enumerate(record['messages']) if message['id'] == user_id), None)
        if user_index is None:
            return
        user = record['messages'][user_index]
        options = user.get('request', {})
        request = GenerationRequest(
            record['id'], user_id, uuid4().hex,
            options.get('provider', self.provider_combo.get()), options.get('model', self.model_combo.get()),
            user.get('prompt', user['content']), options.get('system_prompt', record.get('system_prompt', DEFAULT_SYSTEM_PROMPT)),
            history=tuple(copy.deepcopy(record['messages'][:user_index])),
            attachment_paths=tuple(user.get('attachment_paths', [])) if not user.get('attachments') else (),
            attachments=tuple(copy.deepcopy(user.get('attachments', []))),
            aspect_ratio=options.get('aspect_ratio', '1:1'), count=options.get('count', 1), lyrics=options.get('lyrics', ''),
        )
        self._start_request(request)

    def cancel_request(self):
        self._cancel_session_request(self.current_session_id)

    def _cancel_session_request(self, session_id):
        task_id = self._active_requests.pop(session_id, None)
        task = self._tasks.pop(task_id, None)
        if task is None:
            return
        self.runner.cancel(task_id)
        request = task['request']
        record = self._session_records.get(session_id)
        if record is not None:
            message = next((message for message in record['messages'] if message['id'] == request.assistant_id), None)
            if message is not None:
                partial = message.get('content', '')
                message.update(type='error', status='cancelled', content=(partial + '\n' if partial else '') + '\u8bf7\u6c42\u5df2\u53d6\u6d88\u3002')
            self._persist_record(session_id)
        self.refresh_chat_display()
        self._sync_controls()

    def _poll_tasks(self):
        if self._closing:
            return
        self._process_events()
        self._poll_id = self.after(50, self._poll_tasks)

    def _process_events(self):
        changed = False
        for event in self.runner.drain():
            task = self._tasks.get(event.task_id)
            if task is None:
                continue
            terminal = event.kind in {'result', 'error', 'cancelled'}
            if terminal:
                self._tasks.pop(event.task_id, None)
            if task['kind'] == 'request':
                changed = self._apply_request_event(event, task) or changed
            elif terminal:
                self._apply_auxiliary_event(event, task)
        if changed:
            self.refresh_chat_display()
        self._sync_controls()

    def _apply_request_event(self, event, task):
        request = task['request']
        record = self._session_records.get(request.session_id)
        if record is None or self._active_requests.get(request.session_id) != event.task_id:
            return False
        message = next((message for message in record['messages'] if message['id'] == request.assistant_id), None)
        if message is None:
            return False
        if event.kind == 'prepared':
            user = next(message for message in record['messages'] if message['id'] == request.user_id)
            user['attachments'] = event.payload['attachments']
            user['attachment_paths'] = []
            self._persist_record(record['id'], notify=False)
        elif event.kind == 'delta':
            text, thinking = event.payload
            message['type'] = 'text'
            message['content'] += text
            message['thinking'] += thinking
        elif event.kind == 'progress':
            message['progress'] = event.payload
        elif event.kind == 'context':
            message['dropped_turns'] = event.payload['dropped_turns']
        elif event.kind in {'result', 'error', 'cancelled'}:
            self._active_requests.pop(request.session_id, None)
            if event.kind == 'result':
                result = dict(event.payload)
                for key, thumbnail in result.pop('thumbnails', {}).items():
                    self.renderer.remember_thumbnail(key, thumbnail)
                message.update(result)
                message['status'] = 'complete'
                self._queue_title(record, request)
            else:
                message.update(type='error', status='cancelled' if event.kind == 'cancelled' else 'failed', content=error_message(event.payload) if event.payload else 'Request cancelled')
            self._persist_record(record['id'])
        if message.get('status') == 'pending':
            self._dirty_sessions.add(record['id'])
        if request.session_id == self.current_session_id:
            self.update_status(message.get('progress', '\u51c6\u5907\u5c31\u7eea') if message.get('status') == 'pending' else '\u51c6\u5907\u5c31\u7eea')
            return True
        return False

    def _queue_title(self, record, request):
        if record.get('title_requested') or not CHATLLM_AUTO_TITLE:
            return
        try:
            task_id = self.runner.submit(self.service.title, request.provider, record['messages'][0].get('prompt', request.prompt), owner=record['id'])
        except RuntimeError:
            return
        record['title_requested'] = True
        self._tasks[task_id] = {'kind': 'title', 'session_id': record['id']}

    def _apply_ai_session_title(self, session_id, ai_title):
        record = self._session_records.get(session_id)
        if record is None or self._closing or not ai_title:
            return
        record['title'] = ' '.join(ai_title.split())[:16]
        self._persist_record(session_id)
        self.refresh_listbox_titles()
        if session_id == self.current_session_id:
            self.lbl_session_title.config(text=record['title'])

    def _apply_auxiliary_event(self, event, task):
        record = self._session_records.get(task['session_id'])
        if record is None:
            return
        if task['kind'] == 'title':
            if event.kind == 'result':
                self._apply_ai_session_title(task['session_id'], event.payload)
            else:
                record['title_requested'] = False
        elif task['kind'] == 'thumbnail':
            key = task['key']
            if event.kind == 'result':
                result = dict(event.payload)
                thumbnail = result.pop('thumbnail')
                self.renderer.remember_thumbnail(key, thumbnail)
                self.renderer.remember_thumbnail(result['cache_path'], thumbnail)
                for message in record['messages']:
                    if message['id'] == task['message_id']:
                        for item in message.get('images', []):
                            if self.renderer.thumbnail_key(item) == key:
                                item.update(result)
                        self._persist_record(record['id'], notify=False)
            else:
                self.renderer.thumbnail_failed(key, error_message(event.payload))
            if record['id'] == self.current_session_id:
                self.refresh_chat_display(force_ids=[task['message_id']])
        elif task['kind'] == 'save':
            if event.kind == 'result':
                messagebox.showinfo('\u4fdd\u5b58\u6210\u529f', str(event.payload), parent=self)
            elif event.kind == 'error':
                messagebox.showerror('\u4fdd\u5b58\u5931\u8d25', error_message(event.payload), parent=self)

    def _load_thumbnail(self, session_id, message_id, item, key):
        snapshot = dict(item)
        snapshot['cache_path'] = self._cached_path(item)
        try:
            task_id = self.runner.submit(lambda context, media: self.media_cache.prepare_image(media, context), snapshot, owner=session_id)
        except RuntimeError as error:
            self.renderer.thumbnail_failed(key, str(error))
            return
        self._tasks[task_id] = {'kind': 'thumbnail', 'session_id': session_id, 'message_id': message_id, 'key': key}

    def _cached_path(self, item):
        path = item.get('cache_path')
        if path:
            candidate = Path(path)
            if candidate.is_file():
                return str(candidate)
            candidate = self.media_cache.directory / candidate.name
            if candidate.is_file():
                return str(candidate)
        return None

    def _open_media(self, item):
        try:
            cached = self._cached_path(item)
            target = Path(cached).resolve().as_uri() if cached else self.media_cache.validate_url(item.get('url', ''))
            webbrowser.open(target)
        except (ProviderError, OSError) as error:
            messagebox.showerror('\u65e0\u6cd5\u6253\u5f00', error_message(error), parent=self)

    def _save_media(self, item, filename):
        extension = '.mp3' if item.get('kind') == 'music' else '.png'
        destination = filedialog.asksaveasfilename(parent=self, initialfile=filename, defaultextension=extension, filetypes=[('Media', '*' + extension)])
        if not destination:
            return
        snapshot = dict(item)
        snapshot['cache_path'] = self._cached_path(item)

        def save(context, media, target):
            source = media.get('cache_path') or self.media_cache.fetch(media.get('url', ''), kind=media.get('kind', 'image'), context=context)
            context.check_cancelled()
            return self.media_cache.save_to(source, target)

        try:
            task_id = self.runner.submit(save, snapshot, destination, owner=self.current_session_id)
        except RuntimeError as error:
            messagebox.showerror('\u4fdd\u5b58\u5931\u8d25', str(error), parent=self)
            return
        self._tasks[task_id] = {'kind': 'save', 'session_id': self.current_session_id}
        self.update_status('\u6b63\u5728\u4fdd\u5b58\u6587\u4ef6...')

    def refresh_chat_display(self, force_ids=()):
        if not self._closing:
            self.renderer.sync(self.current_session_id, self.current_messages, force_ids)

    def _sync_controls(self):
        self._is_processing = self.current_session_id in self._active_requests
        self.btn_send.config(state='disabled' if self._is_processing else 'normal')
        self.btn_cancel.config(state='normal' if self._is_processing else 'disabled')

    def set_controls_state(self, state):
        self._sync_controls()

    def generate_image(self):
        self.send_message()

    def generate_music(self):
        self.send_message()

    def _on_close(self):
        if self._closing:
            return
        if self._active_requests and not messagebox.askyesno('\u9000\u51fa', '\u4ecd\u6709\u8bf7\u6c42\u8fdb\u884c\u4e2d\u3002\u53d6\u6d88\u5e76\u9000\u51fa\uff1f', parent=self):
            return
        for session_id in list(self._active_requests):
            self._cancel_session_request(session_id)
        self._capture_current()
        failed = [session_id for session_id in self._session_records if not self._persist_record(session_id, notify=False)]
        if failed and not messagebox.askyesno('\u4fdd\u5b58\u5931\u8d25', '\u90e8\u5206\u4f1a\u8bdd\u672a\u4fdd\u5b58\u3002\u4ecd\u7136\u9000\u51fa\uff1f', parent=self):
            return
        self._closing = True
        for timer in (self._poll_id, self._save_id):
            if timer is not None:
                self.after_cancel(timer)
        self.runner.shutdown()
        self.renderer.reset()
        self.destroy()


if __name__ == '__main__':
    load_configuration()
    app = ChatLLM_GUI()
    app.mainloop()
