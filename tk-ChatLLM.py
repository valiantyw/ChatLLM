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
import traceback
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
from tkinter import filedialog, font as tkfont, messagebox, ttk
from urllib.parse import urljoin, urlparse
from uuid import uuid4


CHATLLM_CONNECT_TIMEOUT = 10
CHATLLM_READ_TIMEOUT = 120
CHATLLM_AUTO_TITLE = True
MINIMAX_STREAM_MODE = 'auto'
KIND_LABELS = {'chat': '聊天', 'image': '图片', 'music': '音乐'}


def parse_timestamp(value):
    """把会话记录里的 ISO 时间戳解析成 datetime；解析不了就返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def relative_time_label(value, now=None):
    """把时间戳转成紧凑的相对时间，分档与 DeepSeek Harness 的会话行一致。

    <1 分钟「刚刚」，<1 小时「N分钟」，<1 天「N小时」，<30 天「N天」，
    <365 天「N个月」，再久「N年」。会话行用短格式（不带「前」）。
    """
    moment = parse_timestamp(value)
    if moment is None:
        return ''
    seconds = max(0.0, ((now or datetime.now()) - moment).total_seconds())
    if seconds < 60:
        return '刚刚'
    if seconds < 3600:
        return '%d分钟' % int(seconds // 60)
    if seconds < 86400:
        return '%d小时' % int(seconds // 3600)
    if seconds < 30 * 86400:
        return '%d天' % int(seconds // 86400)
    if seconds < 365 * 86400:
        return '%d个月' % int(seconds // (30 * 86400))
    return '%d年' % int(seconds // (365 * 86400))


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
        self._saved_states = {}

    @staticmethod
    def _content_digest(record):
        content = dict(record)
        content.pop('updated_at', None)
        serialized = json.dumps(content, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        return hashlib.sha256(serialized.encode('utf-8')).digest()

    @staticmethod
    def _file_stamp(path):
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def _path(self, session_id):
        if not isinstance(session_id, str) or not session_id or any(character in session_id for character in '/\\\x00:'):
            raise SessionStoreError('会话 ID 无效')
        if session_id in {'.', '..'} or Path(session_id).name != session_id:
            raise SessionStoreError('会话 ID 无效')
        return self.directory / f'{session_id}.json'

    @staticmethod
    def legacy_title(session_id):
        parts = session_id.split('-', 4)
        title = parts[4] if len(parts) == 5 else ''
        return title if title and not title.isdigit() else ''

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
                raise ValueError('期望得到包含 messages 列表的会话对象')
            saved_state = self._content_digest(record), self._file_stamp(path)
            for field in ('title', 'provider', 'model', 'system_prompt', 'draft', 'draft_lyrics'):
                if field in record and not isinstance(record[field], str):
                    raise ValueError(f'会话字段无效：{field}')
            if not isinstance(record.get('draft_attachments', []), list) or not all(isinstance(path, str) for path in record.get('draft_attachments', [])):
                raise ValueError('草稿附件无效')
            last_user_id = None
            message_ids = set()
            for message in record['messages']:
                if not isinstance(message, dict) or message.get('role') not in {'user', 'assistant', 'system'}:
                    raise ValueError('消息无效')
                if not isinstance(message.get('content', ''), str):
                    raise ValueError('消息内容无效')
                for field in ('type', 'thinking', 'prompt', 'effective_prompt', 'lyrics', 'model', 'provider', 'progress', 'audio_url', 'cache_path'):
                    if message.get(field) is not None and not isinstance(message[field], str):
                        raise ValueError(f'消息字段无效：{field}')
                if message.get('type') is None:
                    message['type'] = 'text'
                for field in ('images', 'attachments'):
                    items = message.get(field, [])
                    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                        raise ValueError(f'消息字段无效：{field}')
                for image in message.get('images', []):
                    if any(image.get(field) is not None and not isinstance(image[field], str) for field in ('url', 'cache_path')):
                        raise ValueError('图片引用无效')
                for attachment in message.get('attachments', []):
                    if not all(isinstance(attachment.get(field), str) for field in ('name', 'path', 'mime')):
                        raise ValueError('附件引用无效')
                if not isinstance(message.get('request', {}), dict):
                    raise ValueError('保存的请求无效')
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
                    message['content'] = (partial + '\n' if partial else '') + '上一次请求已中断，可以重试。'
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
            self._saved_states[session_id] = saved_state
            return record
        except (OSError, ValueError, TypeError) as error:
            raise SessionStoreError(f'无法读取会话 {path.name}: {error}') from error

    @staticmethod
    def summary(record):
        return {field: record[field] for field in ('id', 'title', 'created_at', 'updated_at')}

    def scan(self, summaries_only=False):
        self.issues = []
        records = []
        for path in self.directory.glob('*.json'):
            if path.name == 'index.json':
                continue
            try:
                record = self.load(path.stem)
                records.append(self.summary(record) if summaries_only else record)
            except SessionStoreError as error:
                self.issues.append(str(error))
        return sorted(records, key=lambda record: str(record['created_at']), reverse=True)

    def save(self, record):
        path = self._path(record['id'])
        temporary_path = None
        try:
            snapshot = dict(record)
            snapshot['schema_version'] = self.SCHEMA_VERSION
            digest = self._content_digest(snapshot)
            if self._saved_states.get(record['id']) == (digest, self._file_stamp(path)):
                return
            snapshot['updated_at'] = datetime.now().isoformat()
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=self.directory,
                prefix=f'.{path.stem}-', suffix='.tmp', delete=False,
            ) as target:
                temporary_path = Path(target.name)
                json.dump(snapshot, target, ensure_ascii=False, indent=2)
                target.write('\n')
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
            record['updated_at'] = snapshot['updated_at']
            self._saved_states[record['id']] = digest, self._file_stamp(path)
        except (OSError, ValueError, TypeError) as error:
            raise SessionStoreError(f'无法保存会话 {path.name}: {error}') from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def delete(self, session_id):
        try:
            self._path(session_id).unlink(missing_ok=True)
            self._saved_states.pop(session_id, None)
        except OSError as error:
            raise SessionStoreError(f'无法删除会话：{error}') from error


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
            raise TaskCancelled('任务已取消')

    def emit(self, kind, payload):
        self.check_cancelled()
        self._events.put(TaskEvent(self.task_id, kind, payload))


class TaskRunner:
    def __init__(self, max_workers=4, max_pending=16, name='chat'):
        self._jobs = queue.Queue(maxsize=max_pending)
        self._events = queue.SimpleQueue()
        self._tasks = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._limit = max_pending
        self._threads = [
            threading.Thread(target=self._worker, name=f'chatllm-{name}-{index}', daemon=True)
            for index in range(max_workers)
        ]
        for worker in self._threads:
            worker.start()

    def submit(self, work, *arguments, owner=None):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('任务执行器已关闭')
            if len(self._tasks) >= self._limit:
                raise RuntimeError('待处理任务过多，请等某个任务完成后再试。')
            task_id = uuid4().hex
            context = TaskContext(task_id, threading.Event(), self._events)
            try:
                self._jobs.put_nowait((context, work, arguments))
            except queue.Full as error:
                # 只在成功入队后登记，否则队列满时会在 _tasks 里永久残留一条记录。
                raise RuntimeError('待处理任务过多，请等某个任务完成后再试。') from error
            self._tasks[task_id] = (context, owner)
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
            raise ProviderError('媒体下载需要不带凭据的 HTTPS 地址')
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
            raise ProviderError('图片无效或过大') from error

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
                        raise ProviderError('媒体跳转缺少目标地址')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise ProviderError(f'媒体下载失败（HTTP {response.status_code})')
                length = response.headers.get('Content-Length')
                if length:
                    try:
                        declared = int(length)
                    except (TypeError, ValueError):
                        declared = None
                    if declared is not None and declared > self.max_bytes:
                        raise ProviderError('媒体下载超过大小上限')
                data = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if context is not None:
                        context.check_cancelled()
                    if len(data) + len(chunk) > self.max_bytes:
                        raise ProviderError('媒体下载超过大小上限')
                    data.extend(chunk)
                if not data:
                    raise ProviderError('媒体下载内容为空')
                return bytes(data)
            finally:
                response.close()
        raise ProviderError('媒体跳转次数过多')

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
            raise ProviderError('媒体数据为空或超过大小上限')
        if kind == 'image':
            data = self.image_bytes(data)
        if len(data) > self.max_bytes:
            raise ProviderError('解码后的媒体超过大小上限')
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
                raise ProviderError('解码后的媒体超过大小上限')
            if context is not None:
                context.check_cancelled()
            self._write_atomic(path, data)
        return str(path)

    def save_to(self, source_path, destination):
        source = Path(source_path)
        if not source.is_file() or source.stat().st_size > self.max_bytes:
            raise ProviderError('缓存媒体不可用或过大')
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
            raise ProviderError(f'最多可添加 {MAX_FILES} 个附件')
        total = 0
        images = 0
        for filename in paths:
            path = Path(filename)
            extension = path.suffix.lower()
            if not path.is_file():
                raise ProviderError(f'附件不可用：{path.name}')
            if extension in TEXT_EXTENSIONS and spec.kind == 'chat':
                limit = MAX_TEXT_BYTES
            elif extension in IMAGE_EXTENSIONS and (spec.images or spec.reference_images):
                limit = MAX_IMAGE_BYTES
                images += 1
            elif extension in VIDEO_MIMES and spec.video:
                limit = MAX_VIDEO_BYTES
                images += 1
            else:
                raise ProviderError(f'当前模型不支持该附件：{path.name}')
            size = path.stat().st_size
            if not 0 < size <= limit:
                raise ProviderError(f'附件为空或过大：{path.name}')
            total += size
        image_limit = 1 if spec.kind == 'image' else spec.max_images
        if images > image_limit or total > MAX_TOTAL_BYTES:
            raise ProviderError('附件数量或总大小超过模型上限')

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
                raise ProviderError(f'附件已变化或超过大小上限：{path.name}')
            if is_text:
                try:
                    data.decode('utf-8-sig')
                except UnicodeDecodeError as error:
                    raise ProviderError(f'文本附件必须使用 UTF-8 编码：{path.name}') from error
                extension, mime = '.txt', 'text/plain'
            elif is_video:
                extension, mime = path.suffix.lower(), VIDEO_MIMES[path.suffix.lower()]
            else:
                data = MediaCache.image_bytes(data)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ProviderError(f'解码后的图片过大：{path.name}')
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
            raise ProviderError(f"附件快照不可用：{attachment.get('name', 'unknown')}")
        limit = MAX_TEXT_BYTES if attachment['mime'].startswith('text/') else (MAX_VIDEO_BYTES if attachment['mime'].startswith('video/') else MAX_IMAGE_BYTES)
        with path.open('rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ProviderError('附件快照超过大小上限')
        return data

    def content(self, message, spec):
        text = message.get('prompt', message.get('content', ''))
        images = []
        for attachment in message.get('attachments', []):
            if attachment['mime'].startswith('text/'):
                text += f"\n\n[附件: {attachment['name']}]\n{self.read(attachment).decode('utf-8-sig')}"
            elif (attachment['mime'].startswith('image/') and spec.images) or (attachment['mime'].startswith('video/') and spec.video):
                images.append((base64.b64encode(self.read(attachment)).decode('ascii'), attachment['mime']))
            else:
                text += f"\n[当前模型不支持该图片附件：{attachment['name']}]"
        return text, images


VIDEO_TOKEN_COST = 8192
IMAGE_TOKEN_COST = 5120


def media_token_cost(mime):
    return VIDEO_TOKEN_COST if mime.startswith('video/') else IMAGE_TOKEN_COST


def _text_token_cost(text):
    """在不引入分词器的前提下近似估算 token 数。

    Latin-1 之外的字符（中日韩、谚文、假名、西里尔、希腊、阿拉伯、emoji）
    大致每个字符算一个 token；Latin-1 文本大致每四个字符算一个 token。
    旧实现返回的是 UTF-8 字节长度，把英文高估约 5 倍、中文高估约 3.5 倍，
    因而静默丢弃了各模型本可容纳的大部分上下文。
    """
    if not text:
        return 0
    wide = len(text) - len(text.encode('latin-1', 'ignore'))
    return wide + (len(text) - wide + 3) // 4


def estimate_tokens(content):
    if isinstance(content, list):
        return sum(
            VIDEO_TOKEN_COST if part.get('type') == 'video_url'
            else (IMAGE_TOKEN_COST if part.get('type') == 'image_url'
                  else _text_token_cost(part.get('text', '')))
            for part in content
        ) + 8
    return _text_token_cost(str(content)) + 8


def build_history(messages, store, spec, current_text, current_images, system_prompt):
    remaining = spec.context_budget - estimate_tokens(system_prompt) - estimate_tokens(current_text) - sum(media_token_cost(mime) for encoded, mime in current_images) - 2048
    if remaining < 0:
        raise ProviderError('当前提示词与附件超出上下文预算，请减小附件或缩短提示词。')
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
    explicit = re.search(r'(?<!\d)(\d{1,2})\s*[:比]\s*(\d{1,2})(?!\d)', prompt)
    if explicit:
        aspect = f'{int(explicit.group(1))}:{int(explicit.group(2))}'
    else:
        hints = {
            '头像': '1:1', '壁纸': '16:9',
            '横图': '16:9', '横版': '16:9',
            '竖图': '9:16', '竖版': '9:16',
        }
        for hint, ratio in hints.items():
            if hint in prompt:
                aspect = ratio
                break
    numbers = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    match = re.search(r'(?<!\d)(\d+|[一两二三四五六七八九十])\s*(?:张|幅)', prompt)
    if match is None:
        match = re.search(r'(?:数量|张数)\s*[:：]?\s*(\d+|[一两二三四五六七八九十])', prompt)
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
        provider_settings(request.provider, kind=spec.kind)
        context.check_cancelled()
        attachments = list(request.attachments)
        if request.attachment_paths:
            context.emit('progress', '正在准备附件')
            attachments = self.attachments.ingest(request.attachment_paths, spec, context)
        context.emit('prepared', {'attachments': attachments})
        current = {'role': 'user', 'prompt': request.prompt, 'attachments': attachments}
        if spec.kind == 'chat':
            prompt, images = self.attachments.content(current, spec)
            history, dropped = build_history(request.history, self.attachments, spec, prompt, images, request.system_prompt)
            context.emit('context', {'dropped_turns': dropped})
            context.emit('progress', '正在生成回复')
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
            context.emit('progress', '正在生成图片')
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
                raise ProviderError('生成音乐需要文本模型或自定义歌词')
            context.emit('progress', '正在创作歌词')
            lyrics, _ = call_chat_api(
                request.provider, text_model, [], refined_prompt, [],
                '根据用户给出的主题写一首简短的原创中文歌曲，只输出歌词，'
                '每行一句，共 10 到 15 行。不要输出标题、说明或段落标签。',
                cancel_event=context.cancelled,
            )
            if not lyrics.strip():
                raise ProviderError('歌词返回为空，请填写自定义歌词后重试。')
        context.check_cancelled()
        context.emit('progress', '正在生成音乐')
        result = call_music_api(request.provider, refined_prompt, lyrics, request.model, 44100)
        data = result.get('data') or {}
        status = data.get('status', 2)
        audio_url = data.get('audio', '')
        if status != 2 or not audio_url:
            raise ProviderError('音乐生成未返回已完成的音频文件')
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
            f'结合之前的对话，把最新的请求改写为详细的{KIND_LABELS.get(kind, kind)}生成提示词。'
            '保留用户要求的改动，只输出最终提示词，不要添加解释。'
        )
        history, dropped = build_history(request.history, self.attachments, spec, request.prompt, [], system_prompt)
        context.emit('context', {'dropped_turns': dropped})
        context.emit('progress', '正在整理生成提示词')
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
            '写一个不超过 16 个字的中文会话标题，只输出标题。',
            cancel_event=context.cancelled,
        )
        return ' '.join(result.strip().strip('"\'').split())[:16]


class ImageGallery(ttk.Frame):
    GAP = 8

    def __init__(self, master, previews, open_media, save_media, position=0):
        super().__init__(master)
        self.position = position
        self._previews = previews
        self._open_media = open_media
        self._save_media = save_media
        self._tiles = []
        self._photos = []
        self._offsets = []
        self._scroll_width = 1
        self._limit = 0
        self._width = None
        self.pack_propagate(False)
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0, background='#ffffff', takefocus=True)
        self.canvas.pack(fill='both', expand=True)
        self.previous = ttk.Button(self, text='◀', width=3, command=lambda: self.move(-1))
        self.next = ttk.Button(self, text='▶', width=3, command=lambda: self.move(1))
        self._max_image_height = 240
        self.configure(height=self._max_image_height)
        total = len(previews)
        for index, (item, thumbnail, status) in enumerate(previews):
            tile = ttk.Frame(self.canvas)
            tile.pack_propagate(False)
            # 序号贴在图片正下方，先占住底部，剩余空间留给图片本身。
            caption = ttk.Label(tile, text='%d/%d' % (index + 1, total), anchor='center',
                                font=('Microsoft YaHei', 8), foreground='#64748b')
            caption.pack(side='bottom', fill='x')
            preview = ttk.Label(tile, text=status, anchor='center', justify='center', foreground='#b91c1c' if status and thumbnail is None else '#64748b')
            preview.pack(fill='both', expand=True)
            preview.bind('<Button-1>', lambda event: self.canvas.focus_set())
            preview.bind('<Double-Button-1>', lambda event, item=item: self._open_image(item))
            preview.bind('<Button-3>', lambda event, item=item, index=index: self._show_save_menu(event, item, index))
            window = self.canvas.create_window(0, 0, window=tile, anchor='nw')
            self._tiles.append((window, preview, caption))
        widgets = [self]
        while widgets:
            widget = widgets.pop()
            for sequence in ('<MouseWheel>', '<Shift-MouseWheel>', '<Button-4>', '<Button-5>'):
                widget.bind(sequence, self._on_wheel)
            for sequence in ('<Enter>', '<Motion>', '<Leave>'):
                widget.bind(sequence, self._update_navigation)
            widgets.extend(widget.winfo_children())
        self.canvas.bind('<Left>', lambda event: self.move(-1))
        self.canvas.bind('<Right>', lambda event: self.move(1))
        self.bind('<Configure>', lambda event: self._update_navigation())
        self._save_menu = tk.Menu(self, tearoff=False)
        self._save_menu.add_command(label='保存原图...')

    def _rightmost_position(self, visible):
        """仍能让末尾一张完整可见的最大起始序号。"""
        threshold = self._scroll_width - max(1, visible)
        limit = 0
        for index, offset in enumerate(self._offsets):
            if offset <= threshold:
                limit = index
        return limit

    def resize(self, width):
        if width == self._width:
            return
        self._width = width
        self.configure(width=width)
        caption_height = max(1, self._tiles[0][2].winfo_reqheight()) if self._tiles else 18
        photos = []
        offsets = []
        x = 0
        tallest = 1
        for index, ((window, preview, _caption), (_, thumbnail, _)) in enumerate(zip(self._tiles, self._previews)):
            if thumbnail is None:
                # 还没有缩略图（加载中或失败），先占一个占位块放状态文字。
                tile_width, image_height = max(1, width // 2), self._max_image_height
            else:
                natural_width, natural_height = thumbnail.size
                scale = min(width / natural_width, self._max_image_height / natural_height, 1.0)
                tile_width = max(1, int(round(natural_width * scale)))
                image_height = max(1, int(round(natural_height * scale)))
            tile_height = image_height + caption_height
            offsets.append(x)
            self.canvas.coords(window, x, 0)
            # 背景块按缩放后的图片尺寸收紧，图片四周不再留出多余底色。
            self.canvas.itemconfigure(window, width=tile_width, height=tile_height)
            preview.configure(wraplength=max(1, tile_width - 8))
            if thumbnail is not None:
                photo = ImageTk.PhotoImage(
                    thumbnail.resize((tile_width, image_height), Image.Resampling.LANCZOS), master=self)
                photos.append(photo)
                preview.configure(image=photo, text='')
            x += tile_width + self.GAP
            tallest = max(tallest, tile_height)
        self._offsets = offsets
        self._scroll_width = max(width, x - self.GAP) if offsets else width
        self.canvas.configure(scrollregion=(0, 0, self._scroll_width, tallest))
        self.configure(height=tallest)
        self._photos = photos
        self._limit = self._rightmost_position(width)
        self.move(0)

    def move(self, step):
        self.position = min(self._limit, max(0, self.position + step))
        offset = self._offsets[self.position] if self._offsets else 0
        self.canvas.xview_moveto(offset / self._scroll_width if self._scroll_width else 0)
        self.previous.configure(state='disabled' if self.position == 0 else 'normal')
        self.next.configure(state='disabled' if self.position >= self._limit else 'normal')
        self._update_navigation()
        return 'break'

    def _update_navigation(self, event=None):
        pointer_x, pointer_y = (event.x_root, event.y_root) if event is not None else self.winfo_pointerxy()
        local_x, local_y = pointer_x - self.winfo_rootx(), pointer_y - self.winfo_rooty()
        inside = self.winfo_ismapped() and 0 <= local_x < self.winfo_width() and 0 <= local_y < self.winfo_height()
        if inside and local_x < 40 and self.position > 0:
            self.previous.place(x=4, rely=0.5, anchor='w', width=32, height=48)
            self.previous.lift()
        else:
            self.previous.place_forget()
        if inside and local_x >= self.winfo_width() - 40 and self.position < self._limit:
            self.next.place(relx=1, x=-4, rely=0.5, anchor='e', width=32, height=48)
            self.next.lift()
        else:
            self.next.place_forget()

    def _open_image(self, item):
        self._open_media(item)
        return 'break'

    def _show_save_menu(self, event, item, index):
        self._save_menu.entryconfigure(0, command=lambda: self._save_media(item, f'image-{index + 1}.png'))
        try:
            self._save_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._save_menu.grab_release()
        return 'break'

    def _on_wheel(self, event):
        if event.num == 4 or event.delta > 0:
            self.move(-1)
        elif event.num == 5 or event.delta < 0:
            self.move(1)
        return 'break'

    def destroy(self):
        self._photos.clear()
        super().destroy()


class ChatRenderer:
    def __init__(self, text, open_media, save_media, load_thumbnail, retry_request):
        self.text = text
        self.open_media = open_media
        self.save_media = save_media
        self.load_thumbnail = load_thumbnail
        self.retry_request = retry_request
        self.session_id = None
        self._ids = []
        self._rendered = set()
        self._stale = set()
        self._windows = {}
        self._images = {}
        self._galleries = {}
        self._gallery_positions = {}
        self._thumbnails = OrderedDict()
        self._active_thumbnail_keys = set()
        self._pending = set()
        self._thumbnail_errors = {}
        self._configure_tags()
        self.text.bind('<Configure>', self._resize_galleries, add='+')

    def _configure_tags(self):
        self.text.tag_configure('user', foreground='#64748b', font=('Microsoft YaHei', 9), spacing1=14, spacing3=4, justify='right')
        self.text.tag_configure('user_body', foreground='#0f172a', background='#e0f2fe', spacing2=3, spacing3=10, lmargin1=60, lmargin2=60, rmargin=12, justify='right')
        self.text.tag_configure('assistant', foreground='#64748b', font=('Microsoft YaHei', 9), spacing1=14, spacing3=4)
        self.text.tag_configure('assistant_body', foreground='#0f172a', background='#f1f5f9', spacing2=3, spacing3=10, lmargin1=12, lmargin2=12, rmargin=30)
        self.text.tag_configure('thinking', foreground='#71717a', font=('Microsoft YaHei', 9), spacing3=8)
        self.text.tag_configure('error', foreground='#b91c1c', spacing1=6, spacing3=8)
        self.text.tag_configure('info', foreground='#64748b', spacing3=6)
        # 图片参数跟在提示词后面，两者共用助手气泡的底色，只把字压暗一档。
        # 这个标签不设 background，气泡才会连成一块。
        self.text.tag_configure('image_params', foreground='#64748b')
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
        self._rendered.clear()
        self._stale.clear()
        self._images.clear()
        self._gallery_positions.clear()
        self.session_id = session_id

    def _destroy_message_widgets(self, message_id):
        gallery = self._galleries.pop(message_id, None)
        if gallery is not None:
            self._gallery_positions[message_id] = gallery.position
        for widget in self._windows.pop(message_id, []):
            widget.destroy()
        self._images.pop(message_id, None)

    def shutdown(self):
        """在解释器仍然存活时释放全部 Tk 图像对象。

        ``ImageTk.PhotoImage.__del__`` 会回调 Tcl；若某个守护工作线程恰好
        触发了这次回收，Tk 会以
        'Tcl_AsyncDelete: async handler deleted by the wrong thread'
        直接终止整个进程。在 UI 线程上提前清空这些引用即可避免。
        """
        self.reset()
        self._thumbnails.clear()
        self._pending.clear()
        self._thumbnail_errors.clear()

    def invalidate(self, *message_ids):
        """把消息标记为需要重绘。

        调用方是就地修改消息字典的，本来就知道改了哪几条，因此由它们主动声明。
        此前 ``sync`` 要靠每 50 毫秒轮询一次、对每条消息做 ``json.dumps``
        来判断变化，长对话中每个 tick 要花 12-19 毫秒。
        凡是就地修改消息的地方都必须调用本方法。
        """
        self._stale.update(message_id for message_id in message_ids if message_id)

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
        stale = self._stale
        self._stale = set()
        stale.update(force_ids)
        self.text.config(state='normal')
        try:
            for message_index, message in enumerate(messages):
                message_id = message['id']
                if message_id in self._rendered and message_id not in stale:
                    continue
                start_mark, end_mark = f'start_{message_id}', f'end_{message_id}'
                next_start = None
                if message_id in self._rendered:
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
                self._rendered.add(message_id)
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
            self._insert('用户\n', 'user')
            self._insert(message.get('content', '') + '\n', 'user_body')
            return
        if role == 'system':
            self._insert(message.get('content', '') + '\n', 'system')
            return
        self._insert((message.get('model') or '助手') + '\n', 'assistant')
        if kind == 'error':
            self._insert(message.get('content', '请求失败') + '\n', 'error')
            if message.get('retry_user_id'):
                button = ttk.Button(self.text, text='重试', command=lambda user_id=message['retry_user_id']: self.retry_request(user_id))
                self._embed(message_id, button)
            return
        if message.get('thinking'):
            self._insert(message['thinking'] + '\n', 'thinking')
        if kind in {'text', 'chat_loading'}:
            self._insert(message.get('content') or ('正在生成...' if message.get('status') == 'pending' else ''), 'assistant_body')
            self._insert('\n', 'assistant_body')
        elif kind.endswith('_loading'):
            self._insert(message.get('progress', '正在生成...') + '\n', 'info')
        elif kind == 'image':
            # 比例和张数用方括号括起来，紧跟在提示词后面，不再另起一行。
            prompt = message.get('prompt', '')
            params = ' | '.join(part for part in (message.get('aspect_ratio', ''),
                                                 '%d 张' % len(message.get('images', []))) if part)
            if prompt:
                self._insert(prompt + ' ', 'assistant_body')
            self._insert('[%s]\n' % params, ('assistant_body', 'image_params'))
            images = message.get('images', [])
            if len(images) > 1:
                self._render_image_gallery(message_id, images)
            else:
                for index, item in enumerate(images):
                    self._render_image(message_id, item, index, len(images))
        elif kind == 'music':
            self._insert(message.get('prompt', '') + '\n')
            self._insert(message.get('lyrics', '') + '\n')
            item = {'url': message.get('audio_url', ''), 'cache_path': message.get('cache_path'), 'kind': 'music'}
            self._media_buttons(message_id, item, 'music.mp3')
        if message.get('cache_error'):
            self._insert(message['cache_error'] + '\n', 'error')
        if message.get('dropped_turns'):
            self._insert(f"上下文已省略 {message['dropped_turns']} 轮较早对话\n", 'info')

    def _resize_galleries(self, event=None):
        inset = sum(self.text.winfo_pixels(self.text.cget(option)) for option in ('padx', 'borderwidth', 'highlightthickness'))
        width = max(1, self.text.winfo_width() - 2 * inset - 2)
        for gallery in self._galleries.values():
            gallery.resize(width)

    def _render_image_gallery(self, message_id, items):
        previews = []
        for item in items:
            key = self.thumbnail_key(item)
            thumbnail = self._thumbnails.get(key)
            status = self._thumbnail_errors.get(key, '正在加载图片...' if key else '图片不可用')
            previews.append((dict(item, kind='image'), thumbnail, status))
            if key and thumbnail is None and key not in self._thumbnail_errors and key not in self._pending:
                self._pending.add(key)
                self.load_thumbnail(self.session_id, message_id, dict(item), key)
        gallery = ImageGallery(self.text, previews, self.open_media, self.save_media, self._gallery_positions.get(message_id, 0))
        self._galleries[message_id] = gallery
        self._resize_galleries()
        self._embed(message_id, gallery)

    def _render_image(self, message_id, item, index, total=1):
        key = self.thumbnail_key(item)
        thumbnail = self._thumbnails.get(key)
        if thumbnail is not None:
            image = ImageTk.PhotoImage(thumbnail, master=self.text)
            self._images.setdefault(message_id, []).append(image)
            self.text.image_create('insert_message', image=image)
            self._insert('\n', 'info')
            # 序号紧跟在图片正下方。
            self._insert('%d/%d\n' % (index + 1, total), 'info')
        elif key in self._thumbnail_errors:
            self._insert(self._thumbnail_errors[key] + '\n', 'error')
        elif key:
            self._insert('正在加载图片...\n', 'info')
            if key not in self._pending:
                self._pending.add(key)
                self.load_thumbnail(self.session_id, message_id, dict(item), key)
        self._media_buttons(message_id, dict(item, kind='image'), f'image-{index + 1}.png')

    def _media_buttons(self, message_id, item, filename):
        frame = ttk.Frame(self.text)
        ttk.Button(frame, text='打开', command=lambda: self.open_media(item)).pack(side='left', padx=3)
        ttk.Button(frame, text='保存', command=lambda: self.save_media(item, filename)).pack(side='left', padx=3)
        self._embed(message_id, frame)


DEFAULT_SYSTEM_PROMPT = '你是智能助手，始终用中文回复。'
FONT_UI = 'Microsoft YaHei'
NEW_SESSION = '新会话'
# 旧版本会把未命名会话存成这个英文字面量；一并视为无标题，
# 这样既有会话仍然显示本地化的占位标题。
UNTITLED_TITLES = frozenset({'New conversation', NEW_SESSION})
# 会话历史列表的列宽。会话是一列平铺的条目，永远没有子节点，
# 但 ttk 仍会在树列左侧为展开箭头固定留出空白（HISTORY_TITLE_GUTTER），
# 因此标题放进自己的列，树列压到最窄。
HISTORY_TITLE_GUTTER = 8
HISTORY_TITLE_WIDTH = 120
HISTORY_MIN_TITLE_WIDTH = 40
HISTORY_TIME_WIDTH = 64
HISTORY_MIN_TIME_WIDTH = 56
HISTORY_SELECT_COLOR = '#3b82f6'
# ttk 的标签只作用于整行、没法按列设置字体，时间因此画在时间列正上方的
# 画布上，字号比会话名小两号、颜色也压暗一档。
HISTORY_TIME_FONT_SIZE = 8
HISTORY_TIME_COLOR = '#64748b'
HISTORY_TIME_PADDING = 4


class ChatLLM_GUI(tk.Tk):
    MAX_LOADED_SESSIONS = 8

    def __init__(self, data_dir=None, frameless=True):
        super().__init__()
        self.withdraw()
        self.title('ChatLLM - 智能助手')
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
        # 聊天与后台媒体任务刻意分成两个线程池：媒体下载使用 120 秒读超时，
        # 过去几个慢速缩略图就会占满全部工作线程，把待发送的聊天请求堵在队列里。
        self.runner = TaskRunner(max_workers=4, max_pending=16, name='chat')
        self.media_runner = TaskRunner(max_workers=4, max_pending=48, name='media')
        self._session_records = OrderedDict()
        self._session_summaries = {}
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
        self._sashes_aligned = False
        self._history_times = {}
        self._history_time_font_object = None
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
        # 窗口显示后立即对齐分割线；<Configure> 绑定作为兜底。
        self.after_idle(self._align_sashes)
        if self._frameless:
            self.after(100, self._fix_taskbar)

    def setup_ui(self):
        if self._frameless:
            self._setup_titlebar()
        self.main_paned = tk.PanedWindow(self, orient='horizontal', sashwidth=10, bd=0, bg='#e2e8f0')
        self.main_paned.pack(fill='both', expand=True)
        self.sidebar_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(self.sidebar_frame, width=260, minsize=45)
        self.btn_toggle_sidebar = ttk.Button(self.sidebar_frame, text='◀ 收起侧边栏', command=self.toggle_sidebar)
        self.btn_toggle_sidebar.pack(fill='x', padx=5, pady=5)
        self.sidebar_paned = ttk.PanedWindow(self.sidebar_frame, orient='vertical')
        self.sidebar_paned.pack(fill='both', expand=True, padx=5, pady=5)
        history_frame = ttk.LabelFrame(self.sidebar_paned, text=' 会话历史 ', padding=5)
        self.sidebar_paned.add(history_frame, weight=3)
        actions = ttk.Frame(history_frame)
        actions.pack(fill='x', pady=(0, 6))
        self.btn_new_chat = ttk.Button(actions, text='新建会话', command=self.new_session)
        self.btn_new_chat.pack(side='left', fill='x', expand=True)
        self.btn_delete_chat = ttk.Button(actions, text='删除', command=self.delete_session, width=6)
        self.btn_delete_chat.pack(side='right', padx=(5, 0))
        list_frame = ttk.Frame(history_frame)
        list_frame.pack(fill='both', expand=True)
        # 会话行需要「左边标题 + 右边时间」两列，Listbox 只能放单列文本。
        self.style.configure('History.Treeview', font=(FONT_UI, 10), rowheight=25,
                             background='#fbfbfb', fieldbackground='#fbfbfb', borderwidth=0)
        self.style.map('History.Treeview',
                       background=[('selected', HISTORY_SELECT_COLOR)],
                       foreground=[('selected', '#ffffff')])
        self.history_list = ttk.Treeview(list_frame, columns=('title', 'time'), show='tree',
                                         selectmode='browse', style='History.Treeview')
        # 标题放自己的列、树列压到最窄，标题左侧就不再有一段空白。
        # 标题列的宽度由 _fit_history_columns 按实际宽度回填。
        self.history_list.column('#0', width=1, minwidth=1, stretch=False)
        self.history_list.column('title', width=HISTORY_TITLE_WIDTH,
                                 minwidth=HISTORY_MIN_TITLE_WIDTH, stretch=False)
        self.history_list.column('time', width=HISTORY_TIME_WIDTH,
                                 minwidth=HISTORY_MIN_TIME_WIDTH, stretch=False, anchor='w')
        self.history_list.pack(side='left', fill='both', expand=True)
        list_scroll = ttk.Scrollbar(list_frame, command=self.history_list.yview)
        list_scroll.pack(side='right', fill='y')
        self.list_scroll = list_scroll
        self.history_list.config(yscrollcommand=self._on_history_scrolled)
        self.history_list.bind('<<TreeviewSelect>>', self.on_session_select)
        self.history_list.bind('<<TreeviewSelect>>', self._draw_history_strip, add='+')
        self.history_list.bind('<Button-1>', self._on_history_press)
        self.history_list.bind('<Configure>', self._fit_history_columns)
        # 时间列在 Treeview 里只占位（不写字），真正的文字由这块与之等宽、
        # 等高的画布绘制，好让时间用更小的字号和更淡的颜色。
        self.history_strip = tk.Canvas(list_frame, width=HISTORY_TIME_WIDTH, height=1,
                                       highlightthickness=0, bd=0, background='#fbfbfb')
        self.history_strip.bind('<Button-1>', self._on_history_strip_press)
        self.history_strip.bind('<Configure>', self._draw_history_strip)
        settings = ttk.Frame(self.sidebar_paned, padding=8)
        self.sidebar_paned.add(settings, weight=0)
        ttk.Label(settings, text='提供商:').pack(anchor='w')
        self.provider_combo = ttk.Combobox(settings, state='readonly', values=list(PROVIDERS))
        self.provider_combo.pack(fill='x', pady=(2, 8))
        self.provider_combo.set(DEFAULT_PROVIDER)
        self.provider_combo.bind('<<ComboboxSelected>>', self.update_model_options)
        ttk.Label(settings, text='模型名称:').pack(anchor='w')
        self.model_combo = ttk.Combobox(settings, state='readonly', values=PROVIDERS[DEFAULT_PROVIDER])
        self.model_combo.pack(fill='x', pady=(2, 8))
        self.model_combo.set(DEFAULT_MODEL)
        self.model_combo.bind('<<ComboboxSelected>>', self.on_model_changed)
        ttk.Label(settings, text='系统提示词:').pack(anchor='w')
        self.system_text = tk.Text(settings, height=2, wrap='word', font=(FONT_UI, 9), bd=1, relief='solid')
        self.system_text.pack(fill='both', expand=True, pady=(2, 0))
        self.system_text.insert('1.0', DEFAULT_SYSTEM_PROMPT)
        right = ttk.Frame(self.main_paned)
        self.main_paned.add(right, minsize=420)
        self.lbl_session_title = ttk.Label(right, text=NEW_SESSION, font=(FONT_UI, 10, 'bold'), padding=(10, 8))
        self.lbl_session_title.pack(fill='x')
        self.lbl_session_title.bind('<Configure>', lambda event: self.lbl_session_title.config(wraplength=max(100, event.width - 20)))
        self.chat_paned = chat_paned = ttk.PanedWindow(right, orient='vertical')
        chat_paned.pack(fill='both', expand=True, padx=5, pady=(5, 0))
        display_frame = ttk.Frame(chat_paned)
        chat_paned.add(display_frame, weight=4)
        self.chat_display = tk.Text(display_frame, wrap='word', state='disabled', bd=1, relief='solid', highlightthickness=0, bg='#ffffff', font=(FONT_UI, 10), padx=10, pady=6, cursor='xterm')
        self.chat_display.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(display_frame, command=self.chat_display.yview)
        scroll.pack(side='right', fill='y', before=self.chat_display)
        self.chat_display.config(yscrollcommand=scroll.set)
        self.chat_display.bind('<Control-c>', self._copy_selection)
        self.chat_display.bind('<Control-C>', self._copy_selection)
        self.input_frame = input_frame = ttk.Frame(chat_paned)
        chat_paned.add(input_frame, weight=1)
        attachment_bar = ttk.Frame(input_frame)
        attachment_bar.pack(fill='x', pady=(0, 3))
        self.btn_add_file = ttk.Button(attachment_bar, text='添加附件', command=self.add_file)
        self.btn_add_file.pack(side='left')
        self.btn_clear_attachments = ttk.Button(attachment_bar, text='清除', command=self.clear_attachments, width=5)
        self.btn_clear_attachments.pack(side='left', padx=(4, 16))
        self.lbl_attachments = ttk.Label(attachment_bar, text='', font=(FONT_UI, 9))
        self.lbl_attachments.pack(side='left', fill='x', expand=True)
        self.lbl_attachments.bind('<Configure>', lambda event: self.lbl_attachments.config(wraplength=max(60, event.width)))
        self.input_text = tk.Text(input_frame, height=6, wrap='word', font=(FONT_UI, 10), bd=1, relief='solid', padx=5, pady=5)
        self.input_text.pack(fill='both', expand=True)
        self.input_text.bind('<Return>', self.on_enter_pressed)
        self.input_text.bind('<Shift-Return>', self.on_shift_enter_pressed)
        command_bar = ttk.Frame(input_frame)
        command_bar.pack(side='bottom', fill='x', pady=(4, 0), before=self.input_text)
        # 右侧先排布，窗口变窄时优先保证按钮不会被挤掉。
        if self._frameless:
            ttk.Sizegrip(command_bar).pack(side='right', anchor='se')
        self.btn_send = ttk.Button(command_bar, text='发送', command=self.send_message)
        self.btn_send.pack(side='right')
        self.btn_cancel = ttk.Button(command_bar, text='取消请求', command=self.cancel_request, state='disabled')
        self.btn_cancel.pack(side='right', padx=5)
        self.params_bar = ttk.Frame(command_bar)
        self.params_bar.pack(side='left')
        # 状态文字与按钮同处一行，底部不再单独占用一条状态栏。
        self.status_bar = ttk.Label(command_bar, text='准备就绪', padding=(8, 4), font=(FONT_UI, 9))
        self.status_bar.pack(side='left', fill='x', expand=True)
        self.status_bar.bind('<Configure>', lambda event: self.status_bar.config(wraplength=max(100, event.width - 16)))
        self.lbl_aspect = ttk.Label(self.params_bar, text='比例:')
        self.img_aspect_combo = ttk.Combobox(self.params_bar, state='readonly', width=7)
        self.lbl_n = ttk.Label(self.params_bar, text='张数:')
        self.img_n_combo = ttk.Combobox(self.params_bar, state='readonly', width=3)
        self.btn_edit_lyrics = ttk.Button(self.params_bar, text='添加歌词', command=self.edit_lyrics_popup)
        self.on_model_changed()
        self._setup_sash_grips()
        # 首次布局完成后，把右侧分割线对齐到左侧分割线。
        self.chat_paned.bind('<Configure>', self._align_sashes, add='+')

    def _align_sashes(self, event=None):
        """让「对话内容 / 输入区」的分割线与左侧「会话历史 / 设置」的分割线对齐。

        只在首次布局后执行一次；用户之后手动拖动分割线时不再干预。
        """
        if self._sashes_aligned or self._closing:
            return
        # 首次布局时控件可能尚未 mapped，但尺寸已经算好，因此只校验尺寸。
        if self.chat_paned.winfo_height() < 120 or self.sidebar_paned.winfo_height() < 120:
            return
        self._sashes_aligned = True
        # 左侧分割线的屏幕纵坐标，换算成右侧分割窗格内的相对位置。
        target = (self.sidebar_paned.winfo_rooty() + self.sidebar_paned.sashpos(0)
                  - self.chat_paned.winfo_rooty())
        # 输入区至少要放得下附件栏、输入框和按钮行，对齐不能把它压扁。
        ceiling = max(0, self.chat_paned.winfo_height() - self.input_frame.winfo_reqheight())
        self.chat_paned.sashpos(0, max(0, min(target, ceiling)))

    def _setup_sash_grips(self):
        self._sash_grip = tk.PhotoImage(master=self, width=28, height=10)
        for offset in (4, 12, 20):
            self._sash_grip.put('#64748b', to=(offset + 1, 3, offset + 3, 7))
            self._sash_grip.put('#64748b', to=(offset, 4, offset + 4, 6))
        self.style.element_create('ChatLLM.Sash.grip', 'image', self._sash_grip)
        self.style.configure('Horizontal.Sash', sashthickness=10)
        self.style.layout('Horizontal.Sash', [
            ('Sash.hsash', {'sticky': 'we', 'children': [
                ('ChatLLM.Sash.grip', {'sticky': ''}),
            ]}),
        ])

        paned = self.main_paned
        grip = tk.Canvas(paned, width=10, height=28, bd=0, highlightthickness=0, bg='#e2e8f0', cursor='sb_h_double_arrow')
        for offset in (4, 12, 20):
            grip.create_oval(3, offset, 7, offset + 4, fill='#64748b', outline='')

        def position_grip(event=None):
            grip.place(x=paned.sash_coord(0)[0] + 5, rely=0.5, anchor='center')
            tk.Misc.lift(grip)

        def forward_drag(event, sequence):
            paned.event_generate(sequence, x=event.x_root - paned.winfo_rootx(), y=event.y_root - paned.winfo_rooty(), rootx=event.x_root, rooty=event.y_root, state=event.state)

        for sequence in ('<ButtonPress-1>', '<B1-Motion>', '<ButtonRelease-1>'):
            grip.bind(sequence, lambda event, sequence=sequence: forward_drag(event, sequence))
        paned.bind('<Configure>', position_grip, add='+')
        self.sidebar_frame.bind('<Configure>', position_grip, add='+')
        self.after_idle(position_grip)

    def _setup_titlebar(self):
        bar = tk.Frame(self, bg='#e2e8f0', height=34)
        bar.pack(fill='x')
        bar.pack_propagate(False)
        title = tk.Label(bar, text='ChatLLM - 智能助手', bg='#e2e8f0', fg='#1e293b', font=(FONT_UI, 10, 'bold'))
        title.pack(side='left', padx=10)
        for label, command in [('X', self._on_close), ('□', self._toggle_maximize), ('−', self._minimize)]:
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
            self.btn_toggle_sidebar.config(text='▶')
        else:
            self.sidebar_paned.pack(fill='both', expand=True, padx=5, pady=5)
            self.main_paned.paneconfigure(self.sidebar_frame, width=getattr(self, '_sidebar_width', 260))
            self.btn_toggle_sidebar.config(text='◀ 收起侧边栏')
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

    def _session_view(self, session_id):
        """取会话的展示数据：优先用内存里已加载的记录，否则用启动时的摘要。"""
        return self._session_records.get(session_id, self._session_summaries.get(session_id, {}))

    def _session_title_from_id(self, session_id):
        title = self._session_view(session_id).get('title')
        return title if title and title not in UNTITLED_TITLES else NEW_SESSION

    def _session_time_from_id(self, session_id):
        view = self._session_view(session_id)
        return view.get('updated_at') or view.get('created_at') or ''

    def load_all_sessions(self):
        self._loading_flag = True
        records = self.session_store.scan(summaries_only=True)
        self._session_summaries = {record['id']: record for record in records}
        self._session_records = OrderedDict()
        self.sessions = [record['id'] for record in records]
        self.new_session()
        self.refresh_history_list()
        self._loading_flag = False
        if self.session_store.issues:
            self.update_status(f'{len(self.session_store.issues)} 个历史文件无法读取，原文件已保留。')

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
    def _has_reply(record):
        """会话里是否至少有一条成功完成的模型回复。

        失败、取消、中断的回复都不算。因此新建的会话只有在模型真正给出回复
        之后才会写进磁盘，不会留下一堆没有任何回复的空会话文件。
        """
        for message in record.get('messages') or []:
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                continue
            if message.get('status') != 'complete':
                continue
            kind = message.get('type') or 'text'
            if kind == 'text':
                if isinstance(message.get('content'), str) and message['content'].strip():
                    return True
            elif kind == 'image':
                if message.get('images'):
                    return True
            elif kind == 'music':
                if message.get('audio_url') or message.get('cache_path'):
                    return True
        return False

    def _trim_session_cache(self):
        if len(self._session_records) <= self.MAX_LOADED_SESSIONS:
            return
        protected = {self.current_session_id} | set(self._active_requests) | self._dirty_sessions
        protected.update(task['session_id'] for task in self._tasks.values())
        for session_id in list(self._session_records):
            if len(self._session_records) <= self.MAX_LOADED_SESSIONS:
                break
            if session_id not in protected:
                self._session_records.pop(session_id)

    def _persist_record(self, session_id, notify=True):
        record = self._session_records.get(session_id)
        if record is None or (not self._has_reply(record) and session_id not in self.sessions):
            return True
        self._dirty_sessions.add(session_id)
        try:
            self.session_store.save(record)
            self._session_summaries[session_id] = self.session_store.summary(record)
            self._dirty_sessions.discard(session_id)
            if session_id not in self.sessions:
                self.sessions.insert(0, session_id)
                self.refresh_history_list()
            return True
        except SessionStoreError as error:
            self.update_status('会话保存失败，内容仍保留在内存中。')
            if notify:
                messagebox.showerror('保存失败', str(error), parent=self)
            return False

    def save_session_by_id(self, session_id):
        if session_id == self.current_session_id:
            self._capture_current()
        return self._persist_record(session_id)

    def _autosave(self):
        if self._closing:
            return
        record = self._capture_current()
        pending = set(self._active_requests) | set(self._dirty_sessions)
        if record is not None:
            pending.add(record['id'])
        for session_id in pending:
            self._persist_record(session_id, notify=False)
        self._save_id = self.after(10000, self._autosave)

    def _history_row_height(self):
        return max(1, int(self.style.lookup('History.Treeview', 'rowheight') or 25))

    def _history_time_font(self):
        """时间专用的字体：比会话名小两号。"""
        if self._history_time_font_object is None:
            self._history_time_font_object = tkfont.Font(
                root=self, family=FONT_UI, size=HISTORY_TIME_FONT_SIZE)
        return self._history_time_font_object

    def _draw_history_strip(self, *args):
        """在时间列上补画出小号灰字的时间。

        ttk::treeview 的标签只作用于整行、无法按列设置字体，因此时间列本身
        不写字，文字画在这块与时间列等宽等高的画布上。滚动、选中、改变
        宽度都会重画，画布跟着走。
        """
        strip = self.history_strip
        strip.delete('all')
        rows = self.history_list.get_children('')
        if not rows or strip.winfo_width() <= 1:
            return
        height = self._history_row_height()
        offset = self.history_list.yview()[0] * len(rows) * height
        selected = set(self.history_list.selection())
        bottom, right = strip.winfo_height(), strip.winfo_width()
        font = self._history_time_font()
        for index, session_id in enumerate(rows):
            y = index * height - offset
            if y + height <= 0 or y >= bottom:
                continue
            # 选中高亮由 Treeview 画在整行上，画布这半边需要自己补一块。
            if session_id in selected:
                strip.create_rectangle(0, y, right, y + height,
                                       fill=HISTORY_SELECT_COLOR, width=0)
            text = self._history_times.get(session_id, '')
            if text:
                strip.create_text(right - HISTORY_TIME_PADDING, y + height // 2,
                                  text=text, anchor='e', font=font,
                                  fill='#ffffff' if session_id in selected
                                  else HISTORY_TIME_COLOR)

    def _on_history_scrolled(self, *args):
        self.list_scroll.set(*args)
        self._draw_history_strip()

    def _on_history_strip_press(self, event):
        """点在时间条上等同于点在整行的同一处。"""
        rows = self.history_list.get_children('')
        self.history_list.focus_set()
        if not rows:
            return 'break'
        height = self._history_row_height()
        offset = self.history_list.yview()[0] * len(rows) * height
        index = int((event.y + offset) // height)
        if not 0 <= index < len(rows):
            # 与列表其余空白处一致：只给焦点，选中项保持不动。
            return 'break'
        session_id = rows[index]
        if session_id not in self.history_list.selection():
            self.history_list.selection_set(session_id)
        return 'break'

    def _fit_history_columns(self, event=None):
        """按列表的实际宽度回填「标题 / 时间」两列。

        ttk 的 stretch 只把多出来的宽度分给可拉伸的列，控件变窄时列宽一分也不收，
        于是最右侧的时间列会被裁掉半截。侧边栏默认只有 260 像素宽，
        不跟着重排的话「3小时」只能看到「小时」。
        """
        available = self.history_list.winfo_width()
        if available <= 1:
            return
        room = available - HISTORY_TITLE_GUTTER
        # 侧边栏被拖得很窄时先砍标题，标题实在让不出位置才动时间。
        time_width = min(HISTORY_TIME_WIDTH,
                         max(HISTORY_MIN_TIME_WIDTH, room - HISTORY_MIN_TITLE_WIDTH))
        title_width = max(1, room - time_width)
        if (int(self.history_list.column('title', 'width')) != title_width
                or int(self.history_list.column('time', 'width')) != time_width):
            self.history_list.column('title', width=title_width)
            self.history_list.column('time', width=time_width)
        # 时间条正好盖住时间列；时间列不写字，即使差一两个像素也看不出来。
        self.history_strip.place_configure(
            in_=self.history_list, x=HISTORY_TITLE_GUTTER + title_width, y=0,
            width=time_width, height=self.history_list.winfo_height())
        self._draw_history_strip()

    def refresh_history_list(self):
        # 重建列表时会设置选中项，那是程序行为而不是用户点选；
        # 用 _loading_flag 挡掉由此触发的选择事件（并保留外层的取值）。
        previous = self._loading_flag
        self._loading_flag = True
        try:
            self.history_list.delete(*self.history_list.get_children())
            now = datetime.now()
            self._history_times = {}
            for session_id in self.sessions:
                # 行尾显示紧凑的相对时间，与 DeepSeek Harness 的会话行一致。
                # 时间列本身不写字，标签交给覆盖在上面的时间条绘制。
                label = relative_time_label(self._session_time_from_id(session_id), now)
                self._history_times[session_id] = label
                self.history_list.insert(
                    '', 'end', iid=session_id,
                    values=(self._session_title_from_id(session_id), ''),
                )
            if self.current_session_id in self.sessions:
                self.history_list.selection_set(self.current_session_id)
        finally:
            self._loading_flag = previous
        self._draw_history_strip()

    def new_session(self, provider=None, model=None):
        provider = self.provider_combo.get() if provider is None else provider
        model = self.model_combo.get() if model is None else model
        if self.current_session_id and not self.save_session_by_id(self.current_session_id):
            return
        previous = self._session_records.get(self.current_session_id)
        if previous is not None and not self._has_reply(previous) and previous['id'] not in self.sessions:
            self._session_records.pop(previous['id'], None)
        record = self.session_store.create(provider, model, self.system_text.get('1.0', tk.END).strip())
        self._session_records[record['id']] = record
        self.load_session_by_id(record['id'])

    @staticmethod
    def _session_model_selection(record):
        provider, model = record.get('provider'), record.get('model')
        for message in reversed(record['messages']):
            options = message.get('request', {})
            saved_provider = options.get('provider') or message.get('provider')
            saved_model = options.get('model') or message.get('model')
            saved_provider = saved_provider if isinstance(saved_provider, str) else None
            saved_model = saved_model if isinstance(saved_model, str) else None
            if saved_provider and saved_model:
                provider, model = saved_provider, saved_model
                break
            if provider and model:
                continue
            if provider and saved_provider and canonical_provider(saved_provider) != canonical_provider(provider):
                continue
            if model and saved_model and saved_model != model:
                continue
            provider = provider or saved_provider
            model = model or saved_model
        if not provider and model:
            candidates = [name for name, models in PROVIDERS.items() if model in models]
            if len(candidates) == 1:
                provider = candidates[0]
        provider = canonical_provider(provider or DEFAULT_PROVIDER)
        return provider, model or PROVIDERS.get(provider, (DEFAULT_MODEL,))[0]

    def _set_model_selection(self, provider, model):
        providers = list(PROVIDERS)
        if provider not in providers:
            providers.append(provider)
        models = list(PROVIDERS.get(provider, ()))
        if model not in models:
            models.append(model)
        self.provider_combo['values'] = providers
        self.provider_combo.set(provider)
        self.model_combo['values'] = models
        self.model_combo.set(model)

    def load_session_by_id(self, session_id):
        record = self._session_records.get(session_id)
        if record is None:
            try:
                record = self.session_store.load(session_id)
            except SessionStoreError as error:
                messagebox.showerror('读取失败', str(error), parent=self)
                return
            self._session_records[session_id] = record
            self._session_summaries[session_id] = self.session_store.summary(record)
        self._session_records.move_to_end(session_id)
        self.current_session_id = session_id
        self.current_messages = record['messages']
        provider, model = self._session_model_selection(record)
        self._set_model_selection(provider, model)
        self.system_text.delete('1.0', tk.END)
        self.system_text.insert('1.0', record.get('system_prompt', DEFAULT_SYSTEM_PROMPT))
        self.input_text.delete('1.0', tk.END)
        self.input_text.insert('1.0', record.get('draft', ''))
        self.attached_files = list(record.get('draft_attachments', []))
        self.custom_lyrics = record.get('draft_lyrics', '')
        self.on_model_changed()
        for message in reversed(self.current_messages):
            if message.get('type') == 'image' and model in PROVIDERS.get(provider, ()):
                spec = capabilities(provider, model)
                if message.get('aspect_ratio') in spec.ratios:
                    self.img_aspect_combo.set(message['aspect_ratio'])
                break
        self.lbl_session_title.config(text=self._session_title_from_id(session_id))
        self.refresh_history_list()
        self.refresh_chat_display()
        self._sync_controls()
        self._trim_session_cache()

    def _on_history_press(self, event):
        """点在条目之间的空白处时不改变选中项。

        Treeview 的默认点击绑定会按落点定位行；落在空白处时我们直接拦截，
        这样选中项和当前会话都保持不动。
        """
        if not self.sessions or not self.history_list.identify_row(event.y):
            # 只把焦点交给列表，选中项和当前会话都保持不动。
            self.history_list.focus_set()
            return 'break'
        return None

    def on_session_select(self, event=None):
        if self._loading_flag:
            return
        selection = self.history_list.selection()
        if not selection:
            return
        session_id = selection[0]
        if session_id not in self.sessions:
            return
        if session_id == self.current_session_id:
            # 点到的就是当前会话：不切换内容，只把当前会话保存一次。
            self.save_session_by_id(session_id)
            return
        if self.save_session_by_id(self.current_session_id):
            self.load_session_by_id(session_id)

    def delete_session(self):
        selection = self.history_list.selection()
        if not selection:
            return
        session_id = selection[0]
        if session_id not in self.sessions:
            return
        if not messagebox.askyesno('删除会话', f'确定删除 {self._session_title_from_id(session_id)}？', parent=self):
            return
        try:
            self.session_store.delete(session_id)
        except SessionStoreError as error:
            messagebox.showerror('删除失败', str(error), parent=self)
            return
        self.runner.cancel_owner(session_id)
        self.media_runner.cancel_owner(session_id)
        self._active_requests.pop(session_id, None)
        self._tasks = {task_id: task for task_id, task in self._tasks.items() if task['session_id'] != session_id}
        self._session_records.pop(session_id, None)
        self._session_summaries.pop(session_id, None)
        self._dirty_sessions.discard(session_id)
        self.sessions.remove(session_id)
        if self.current_session_id == session_id:
            self.current_session_id = None
            if self.sessions:
                self.load_session_by_id(self.sessions[0])
            else:
                self.new_session()
        self.refresh_history_list()

    def update_model_options(self, event=None):
        if event is not None and self.provider_combo.get() == self._model_selection[0]:
            return
        models = PROVIDERS[self.provider_combo.get()]
        self.model_combo['values'] = models
        self.model_combo.set(models[0])
        self.on_model_changed(event)

    def _collapse_params_bar(self):
        """参数栏没有内容时把它收起来。

        Tk 在容器的最后一个子控件被 pack_forget 之后不会重新发出尺寸请求，
        参数栏会一直保持上一次的宽度，状态栏因此回不到原来的位置。
        """
        if self.params_bar.pack_slaves():
            return
        self.params_bar.pack_propagate(False)
        self.params_bar.configure(width=1, height=1)

    def on_model_changed(self, event=None):
        selection = (self.provider_combo.get(), self.model_combo.get())
        if event is not None and not self._loading_flag and selection != self._model_selection:
            self._set_model_selection(*self._model_selection)
            self.new_session(*selection)
            return
        self._model_selection = selection
        for widget in (self.lbl_aspect, self.img_aspect_combo, self.lbl_n, self.img_n_combo, self.btn_edit_lyrics):
            widget.pack_forget()
        try:
            spec = capabilities(*selection)
        except ProviderError:
            self._collapse_params_bar()
            self.update_attachments_ui()
            self.update_status('该历史会话的提供商或型号当前不可用。')
            return
        # 重新装入控件前必须恢复尺寸传播，否则参数栏会卡在收起来时的 1 像素。
        self.params_bar.pack_propagate(True)
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
        self.btn_edit_lyrics.config(text='歌词 (已编辑)' if self.custom_lyrics else '添加歌词')
        self._collapse_params_bar()
        self.update_attachments_ui()

    def add_file(self):
        paths = filedialog.askopenfilenames(parent=self, title='添加附件', filetypes=[('支持的文件', '*.txt *.md *.py *.csv *.json *.xml *.yaml *.yml *.png *.jpg *.jpeg *.gif *.bmp *.webp *.mp4 *.avi *.mov *.mkv'), ('所有文件', '*.*')])
        combined = list(dict.fromkeys(self.attached_files + list(paths)))
        try:
            self.attachment_store.validate_paths(combined, capabilities(self.provider_combo.get(), self.model_combo.get()))
        except (ProviderError, OSError) as error:
            messagebox.showerror('附件不可用', error_message(error), parent=self)
            return
        self.attached_files = combined
        self.update_attachments_ui()

    def clear_attachments(self):
        self.attached_files = []
        self.update_attachments_ui()

    def update_attachments_ui(self):
        self.lbl_attachments.config(text=', '.join(Path(path).name for path in self.attached_files) or '未选择附件')
        self.btn_clear_attachments.config(state='normal' if self.attached_files else 'disabled')
        try:
            spec = capabilities(self.provider_combo.get(), self.model_combo.get())
        except ProviderError:
            self.btn_add_file.config(state='disabled')
        else:
            self.btn_add_file.config(state='normal' if spec.kind == 'chat' or spec.reference_images else 'disabled')

    def edit_lyrics_popup(self):
        popup = tk.Toplevel(self)
        popup.title('编辑歌词')
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

        ttk.Button(popup, text='确定', command=save).pack(side='right', padx=10, pady=(0, 10))
        ttk.Button(popup, text='清空', command=lambda: editor.delete('1.0', tk.END)).pack(side='right', padx=5, pady=(0, 10))

    def on_enter_pressed(self, event=None):
        self.send_message()
        return 'break'

    def on_shift_enter_pressed(self, event=None):
        self.input_text.insert(tk.INSERT, '\n')
        return 'break'

    def send_message(self):
        if self.current_session_id in self._active_requests:
            return
        prompt = self.input_text.get('1.0', tk.END).strip()
        if not prompt and not self.attached_files:
            return
        provider, model = self.provider_combo.get(), self.model_combo.get()
        try:
            spec = capabilities(provider, model)
            provider_settings(provider, kind=spec.kind)
            self.attachment_store.validate_paths(self.attached_files, spec)
            if spec.kind != 'chat' and not prompt:
                raise ProviderError('请填写生成提示词')
            aspect, count = '1:1', 1
            if spec.kind == 'image':
                aspect, count = parse_image_options(prompt, self.img_aspect_combo.get(), int(self.img_n_combo.get()))
                if aspect not in spec.ratios or not 1 <= count <= spec.max_count:
                    raise ProviderError(f'图像参数不受支持。可选比例：{", ".join(spec.ratios)}；张数：1-{spec.max_count}')
                self.img_aspect_combo.set(aspect)
                self.img_n_combo.set(str(count))
        except (ProviderError, OSError, ValueError) as error:
            messagebox.showerror('无法发送', error_message(error), parent=self)
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
            content += '\n[附件: ' + ', '.join(Path(path).name for path in self.attached_files) + ']'
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
        user_index = next(
            (index for index, candidate in enumerate(record['messages']) if candidate['id'] == request.user_id),
            None,
        )
        if user_index is None:
            # 用户消息意外消失。这里记录一条可见的错误，而不是让 StopIteration 从
            # 发送流程中抛出，否则会话里会只剩一条提问、完全没有回复。
            message.update(type='error', status='failed', content='请求未发送：找不到对应的用户消息，请重新发送。')
            record['messages'].append(message)
            self._persist_record(record['id'])
            self.refresh_chat_display()
            self._sync_controls()
            return
        insertion = user_index + 1
        while insertion < len(record['messages']) and record['messages'][insertion].get('role') != 'user':
            insertion += 1
        record['messages'].insert(insertion, message)
        if not self._persist_record(record['id']):
            message.update(type='error', status='failed', content='会话保存失败，请求未发送。')
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
        self.refresh_history_list()
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
        self.media_runner.cancel(task_id)
        request = task['request']
        record = self._session_records.get(session_id)
        if record is not None:
            message = next((message for message in record['messages'] if message['id'] == request.assistant_id), None)
            if message is not None:
                partial = message.get('content', '')
                message.update(type='error', status='cancelled', content=(partial + '\n' if partial else '') + '请求已取消。')
                self.renderer.invalidate(message['id'])
            self._persist_record(session_id)
        self.refresh_chat_display()
        self._sync_controls()

    def _poll_tasks(self):
        if self._closing:
            return
        try:
            self._process_events()
        except Exception:
            # 单个畸形事件绝不能让事件泵停摆：否则轮询不再重新排程，窗口会静默卡死，
            # 发送按钮一直禁用，流式输出也不再刷新。
            traceback.print_exc()
            self.update_status('处理后台事件时出错，已跳过该事件。')
        finally:
            if not self._closing:
                self._poll_id = self.after(50, self._poll_tasks)

    def _drain_events(self):
        events = list(self.runner.drain())
        events.extend(self.media_runner.drain())
        return events

    def _process_events(self):
        changed = False
        for event in self._drain_events():
            task = self._tasks.get(event.task_id)
            if task is None:
                continue
            terminal = event.kind in {'result', 'error', 'cancelled'}
            if terminal:
                self._tasks.pop(event.task_id, None)
            try:
                if task['kind'] == 'request':
                    changed = self._apply_request_event(event, task) or changed
                elif terminal:
                    self._apply_auxiliary_event(event, task)
            except Exception:
                # 隔离故障：单个任务结果出错，不应丢弃本批次的其余结果，
                # 也不应让窗口停留在半更新状态。
                traceback.print_exc()
                self.update_status('处理后台任务结果时出错，已跳过该任务。')
        if changed:
            self.refresh_chat_display()
        self._sync_controls()
        self._trim_session_cache()

    def _apply_request_event(self, event, task):
        request = task['request']
        record = self._session_records.get(request.session_id)
        if record is None or self._active_requests.get(request.session_id) != event.task_id:
            return False
        message = next((message for message in record['messages'] if message['id'] == request.assistant_id), None)
        if message is None:
            return False
        if event.kind == 'prepared':
            user = next((item for item in record['messages'] if item['id'] == request.user_id), None)
            if user is not None:
                user['attachments'] = event.payload['attachments']
                user['attachment_paths'] = []
                self.renderer.invalidate(user['id'])
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
                message.update(type='error', status='cancelled' if event.kind == 'cancelled' else 'failed', content=error_message(event.payload) if event.payload else '请求已取消')
            self._persist_record(record['id'])
        if event.kind != 'prepared':
            # 上面每个分支都改动了这条消息，直接通知渲染器重绘，
            # 不必让它靠重新序列化全部内容来判断。
            self.renderer.invalidate(message['id'])
        if message.get('status') == 'pending':
            self._dirty_sessions.add(record['id'])
        if request.session_id == self.current_session_id:
            self.update_status(message.get('progress', '准备就绪') if message.get('status') == 'pending' else '准备就绪')
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
        self.refresh_history_list()
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
                self._dirty_sessions.add(record['id'])
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
                        self.renderer.invalidate(message['id'])
                        self._persist_record(record['id'], notify=False)
            else:
                self.renderer.thumbnail_failed(key, error_message(event.payload))
            if record['id'] == self.current_session_id:
                self.refresh_chat_display(force_ids=[task['message_id']])
        elif task['kind'] == 'save':
            if event.kind == 'result':
                messagebox.showinfo('保存成功', str(event.payload), parent=self)
            elif event.kind == 'error':
                messagebox.showerror('保存失败', error_message(event.payload), parent=self)

    def _load_thumbnail(self, session_id, message_id, item, key):
        snapshot = dict(item)
        snapshot['cache_path'] = self._cached_path(item)
        try:
            task_id = self.media_runner.submit(lambda context, media: self.media_cache.prepare_image(media, context), snapshot, owner=session_id)
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
            messagebox.showerror('无法打开', error_message(error), parent=self)

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
            task_id = self.media_runner.submit(save, snapshot, destination, owner=self.current_session_id)
        except RuntimeError as error:
            messagebox.showerror('保存失败', str(error), parent=self)
            return
        self._tasks[task_id] = {'kind': 'save', 'session_id': self.current_session_id}
        self.update_status('正在保存文件...')

    def refresh_chat_display(self, force_ids=()):
        if not self._closing:
            self.renderer.sync(self.current_session_id, self.current_messages, force_ids)

    def _sync_controls(self):
        self._is_processing = self.current_session_id in self._active_requests
        self.btn_send.config(state='disabled' if self._is_processing else 'normal')
        self.btn_cancel.config(state='normal' if self._is_processing else 'disabled')

    def _on_close(self):
        if self._closing:
            return
        if self._active_requests and not messagebox.askyesno('退出', '仍有请求进行中。取消并退出？', parent=self):
            return
        for session_id in list(self._active_requests):
            self._cancel_session_request(session_id)
        self._capture_current()
        failed = [session_id for session_id in self._session_records if not self._persist_record(session_id, notify=False)]
        if failed and not messagebox.askyesno('保存失败', '部分会话未保存。仍然退出？', parent=self):
            return
        self._closing = True
        for timer in (self._poll_id, self._save_id):
            if timer is not None:
                self.after_cancel(timer)
        self.runner.shutdown()
        self.media_runner.shutdown()
        self.renderer.shutdown()
        # 分割手柄也是一个 Tk 图像对象，它的 __del__ 会回调 Tcl；必须在主线程、且在
        # 解释器销毁之前释放，否则守护线程触发回收会让进程以 Tcl_AsyncDelete 中止。
        self._sash_grip = None
        self.destroy()


def _startup_failure(title, error):
    """以对话框而非裸堆栈的形式报告启动失败。

    SessionStore、MediaCache、AttachmentStore 都在构造时创建目录，
    因此数据目录不可写时，进程会在任何窗口出现之前就直接中止。
    """
    traceback.print_exc()
    detail = error_message(error)
    if isinstance(error, OSError):
        detail += ('\n\nChatLLM 需要在数据目录（默认为程序旁的 conversations 目录）下创建文件和子目录。'
                   '请确认该目录存在，且当前账户对它拥有写入权限。')
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, detail, parent=root)
        root.destroy()
    except Exception:
        print(f'{title}: {detail}', file=sys.stderr)
    return 1


def main():
    try:
        load_configuration()
    except Exception as error:
        return _startup_failure('ChatLLM 配置错误', error)
    try:
        app = ChatLLM_GUI()
    except Exception as error:
        return _startup_failure('ChatLLM 启动失败', error)
    app.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
