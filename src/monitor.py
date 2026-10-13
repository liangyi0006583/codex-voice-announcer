#!/usr/bin/python3
"""Read-only Codex desktop IPC subscriber and macOS speech worker.

No remote model calls, credentials, browser control, turn starts, approval responses,
or writes to Codex databases. Internal IPC versions match the installed app.
"""
import copy
import datetime
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import select
import signal
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import uuid

HOME = Path.home()
DATA = HOME / 'Library/Application Support/CodexVoiceAnnouncer'
CODEX = HOME / '.codex'
DEFAULTS = {'paused': False, 'announce_completed': True, 'rate': 190,
            'voice': 'Tingting', 'speech_limit': 500, 'speech_style': 'formal',
            'pitch_semitones': 0, 'voice_id': 'com.apple.voice.compact.zh-CN.Tingting'}
MAX_FRAME = 268435456
SKIP_FIELDS = {'latestThreadSettings', 'latestCollaborationMode', 'latestTokenUsageInfo', 'currentPermissions', 'canonicalVoiceHistory', 'command', 'aggregatedOutput', 'developer_instructions', 'additionalContext'}


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    temp.write_text(json.dumps(value, ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(path)


def settings():
    return dict(DEFAULTS, **read_json(DATA / 'settings.json', {}))


def clean(text, preserve_lines=False):
    """Make Markdown speakable and remove credential-shaped values."""
    text = str(text or '')
    text = re.sub(r'<oai-mem-citation>.*?</oai-mem-citation>', '', text, flags=re.S)
    text = re.sub(r':::writing[^\n]*|^:::.*$', '', text, flags=re.M)
    text = re.sub(r'::[\w-]+\{[^\n]*\}', '', text)
    text = re.sub(r'```.*?```', '（代码详情请查看任务）', text, flags=re.S)
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '链接请查看任务', text)
    text = re.sub(r'(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+|eyJ[A-Za-z0-9_.-]{40,})', '（敏感信息已省略）', text)
    text = re.sub(r'(?i)((?:api[_ -]?key|client[_ -]?secret|refresh[_ -]?token|access[_ -]?token|password|密码|密钥)\s*[=:：]\s*)[^\s,，;；]+', r'\1（已省略）', text)
    text = re.sub(r'(?m)^\s*[#>*+-]+\s*', '', text)
    text = re.sub(r'[`*_~|]', '', text)
    if preserve_lines:
        text = re.sub(r'[^\S\n]+', ' ', text)
        return re.sub(r'\n{3,}', '\n\n', text).strip()
    return re.sub(r'\s+', ' ', text).strip()


def spoken_words(text):
    text = re.sub(r'\$\s*([\d,]+(?:\.\d+)?)', r'\1 美元', text)
    replacements = {'SP-API': '销售伙伴接口', 'API': '接口', 'FBA': '亚马逊仓储配送',
                    'COGS': '商品成本', 'USD': '美元', 'CNY': '人民币',
                    'TACOS': '广告总销售费用比', 'Fee Preview': '费用预览', 'GitHub CLI': 'GitHub 命令行工具'}
    for token, phrase in replacements.items():
        text = re.sub(r'(?<![A-Za-z])' + re.escape(token) + r'(?![A-Za-z])', phrase, text, flags=re.I)
    text = re.sub(r'\b(\d+)\s+passed\b', r'\1 项测试通过', text)
    return text


def speech_plan(title, kind, content, limit=500, style='formal'):
    title = spoken_words(clean(title))[:70] or '未命名任务'
    content = clean(content, preserve_lines=True)
    if len(content) > limit:
        excerpt = content[:limit]
        stop = max(excerpt.rfind('。'), excerpt.rfind('；'), excerpt.rfind('！'), excerpt.rfind('\n'))
        content = excerpt[:stop + 1] if stop > limit // 2 else excerpt
        content = content.rstrip('。；，\n ') + '。\n完整内容，请打开任务查看。'
    if kind == '需要回答':
        headline = title + '，有一个问题需要您回答。'
    elif kind in ('等待你确认', '计划等待确认'):
        headline = title + '，需要您确认。'
    elif '失败' in kind:
        headline = title + '，执行未成功，请您查看原因。'
    elif kind == '需要你选择':
        headline = title + '，需要您做一个选择。'
    elif kind in ('需要你处理', '需要你操作'):
        headline = title + '，有一项内容需要您处理。'
    elif kind == '待查看（未读）':
        headline = title + '，有一条尚未查看的结果。'
    elif kind == '结果待查看':
        headline = title + '，已完成。请您查看结果。'
    else:
        headline = title + '。'
    if style == 'gentle':
        headline = headline.replace('您', '你')
    formal = style != 'gentle'
    segments = [{'text': '任务提醒。', 'rateMultiplier': 0.93, 'pitch': 1.0 if formal else 1.03, 'pause': 0.32},
                {'text': headline, 'rateMultiplier': 0.95, 'pitch': 1.0, 'pause': 0.48}]
    for paragraph in content.splitlines():
        paragraph = spoken_words(paragraph.strip())
        if not paragraph:
            continue
        # A list item or Markdown heading becomes a spoken sentence.
        if paragraph[-1] not in '。！？；.!?;：:':
            paragraph += '。'
        sentences = [s.strip() for s in re.findall(r'[^。！？；;]+[。！？；;]?', paragraph) if s.strip()]
        for sentence in sentences:
            # Split long clauses at an existing comma, never inside an amount or ID.
            pieces = []
            while len(sentence) > 62:
                positions = [sentence.rfind(mark, 20, 55) for mark in ('，', '、', '：')]
                cut = max(positions)
                if cut < 0:
                    break
                pieces.append(sentence[:cut + 1]); sentence = sentence[cut + 1:].strip()
            pieces.append(sentence)
            for piece in pieces:
                emphasis = bool(re.search(r'请.*(?:确认|提供|选择)|需要.*(?:确认|回答)|未成功|失败原因', piece))
                segments.append({'text': piece, 'rateMultiplier': 0.96 if emphasis else 1.0,
                                 'pitch': 1.0 if formal else 1.025,
                                 'pause': 0.12 if piece.endswith(('，', '、', '：')) else 0.24})
        segments[-1]['pause'] = 0.40
    return segments


def speech_text(title, kind, content, limit=500, style='formal'):
    return '\n'.join(segment['text'] for segment in speech_plan(title, kind, content, limit, style))


def speech_job(cfg, entry, data_dir=DATA):
    segments = speech_plan(entry['title'], entry['kind'], entry.get('speech_source', entry['content']),
                           int(cfg.get('speech_limit', 500)), cfg.get('speech_style', 'formal'))
    voice = cfg.get('voice_id') or DEFAULTS['voice_id']
    rate = max(120, min(300, int(cfg.get('rate', 190))))
    style = cfg.get('speech_style', 'formal')
    tone = max(-3, min(3, float(cfg.get('pitch_semitones', 0))))
    if str(voice).startswith('local:'):
        command = [str(data_dir / 'neural-venv/bin/python'), str(data_dir / 'NeuralEngine.py')]
        payload = {'voiceId': voice, 'text': ' '.join(s['text'] for s in segments[1:]),
                   'language': 'Chinese', 'rate': rate, 'pitchSemitones': tone, 'style': style,
                   'settingsPath': str(data_dir / 'settings.json'),
                   'progressPath': str(data_dir / 'neural-progress.json')}
    else:
        tempo = 0.96 if style == 'gentle' else (1.04 if style == 'firm' else 1)
        offset = 0.65 if style == 'gentle' else (-1.1 if style == 'firm' else 0)
        for segment in segments:
            segment['rateMultiplier'] *= tempo
            segment['pitch'] = 2 ** ((tone + offset) / 12)
            segment['pause'] *= 1.12 if style == 'gentle' else (0.85 if style == 'firm' else 1)
        command = [str(data_dir / 'SpeechEngine')]
        payload = {'voiceId': voice, 'rate': rate, 'segments': segments}
    return command, payload


def turns_from(state):
    result = list(state.get('turns') or [])
    history = state.get('turnHistory') or {}
    entities = (history.get('history') or {}).get('entitiesByKey') or {}
    result.extend(v for v in entities.values() if isinstance(v, dict) and 'items' in v)
    # Use actual start time; completed history can contain many past turns.
    return sorted(result, key=lambda t: t.get('turnStartedAtMs') or t.get('startedAt') or 0)


def all_items(state):
    return [i for t in turns_from(state) for i in t.get('items', []) if isinstance(i, dict)]


def final_text(turn):
    agents = [i for i in turn.get('items', []) if i.get('type') == 'agentMessage']
    finals = [i.get('text', '') for i in agents if i.get('phase') in ('final', 'final_answer')]
    if finals:
        return '\n'.join(finals)
    return agents[-1].get('text', '') if agents else ''


def completion_kind(text):
    if re.search(r'需要.{0,8}(?:你|用户).{0,8}(?:确认|提供|操作|批准)|请.{0,8}(?:确认|提供|选择)|等待.{0,8}(?:批准|确认)|blocked|需要人工', text, re.I):
        return '需要你处理'
    return '结果待查看'


def unread_local_ids(global_state):
    """Read the desktop's actual unread index, including unloaded chats."""
    index = global_state.get('electron-thread-read-state-v1') or {}
    buckets = index.get('unreadByIdentity') or {}
    result = set()
    for hosts in buckets.values():
        if not isinstance(hosts, dict):
            continue
        for host_identity, ids in hosts.items():
            if host_identity.startswith('local:') and isinstance(ids, list):
                result.update(t for t in ids if isinstance(t, str))
    return result


def request_detail(request):
    method = request.get('method', '')
    p = request.get('params') or {}
    questions = p.get('questions') or []
    if questions:
        texts = []
        for q in questions:
            texts.append(q.get('question') or q.get('title') or q.get('prompt') or '')
            opts = q.get('options') or []
            labels = [o.get('label', '') if isinstance(o, dict) else str(o) for o in opts]
            if labels:
                texts.append('可选：' + '；'.join(labels))
        return '需要回答', '。'.join(texts)
    if 'Approval' in method or 'approval' in method:
        detail = p.get('reason') or p.get('justification') or ''
        if not detail:
            if 'fileChange' in method:
                detail = '请查看文件修改并决定是否允许。'
            elif 'permissions' in method:
                detail = '请查看所请求的权限并决定是否允许。'
            else:
                detail = '请查看待执行操作并决定是否允许。'
        # Never read raw shell commands: they may contain credentials.
        return '等待你确认', detail
    if method == 'mcpServer/elicitation/request':
        return '需要你操作', p.get('message') or '请在任务中完成连接、登录或信息填写。'
    if method == 'item/plan/requestImplementation':
        return '计划等待确认', p.get('message') or '请查看计划并决定是否开始实施。'
    if 'OptionPicker' in method:
        return '需要你选择', p.get('title') or p.get('message') or '请查看任务中的选项。'
    if method == 'item/tool/call':
        args = p.get('arguments') or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        tool = p.get('tool', '')
        if any(word in tool.lower() for word in ('user_input', 'option_picker', 'setup_codex', 'request_user_input')):
            return request_detail({'method': 'item/tool/requestUserInput', 'params': args}) if args.get('questions') else ('需要你操作', args.get('title') or '请查看任务中的待处理提示。')
    return None


def trim_state(value):
    """Keep patch indices, but discard command output and hidden reasoning."""
    if isinstance(value, list):
        return [trim_state(v) for v in value]
    if not isinstance(value, dict):
        return value
    if value.get('type') in ('commandExecution', 'mcpToolCall', 'reasoning', 'fileChange', 'functionCallOutput', 'webSearch', 'imageGeneration'):
        return {k: value[k] for k in ('type', 'id', 'status') if k in value}
    discard = {'params'} if 'items' in value and 'turnId' in value else set()
    discard |= SKIP_FIELDS | {'arguments'}
    # Preserve tool-call request arguments so user-facing questions can be read.
    if value.get('method') == 'item/tool/call':
        return copy.deepcopy(value)
    return {k: trim_state(v) for k, v in value.items() if k not in discard}


def apply_patches(state, patches):
    for patch in patches:
        path = patch.get('path')
        if isinstance(path, str):
            path = [p.replace('~1', '/').replace('~0', '~') for p in path.strip('/').split('/')]
        if path and (any(p in SKIP_FIELDS for p in path) or ('arguments' in path and 'requests' not in path)):
            continue
        if not path:
            if patch['op'] in ('replace', 'add'):
                state = trim_state(patch['value'])
            continue
        target = state
        for part in path[:-1]:
            if isinstance(target, list):
                target = target[int(part)]
            else:
                target = target[part]
        key = int(path[-1]) if isinstance(target, list) else path[-1]
        op = patch['op']
        if op == 'remove':
            if isinstance(target, list):
                target.pop(key)
            else:
                target.pop(key, None)
        elif op == 'add' and isinstance(target, list):
            target.insert(key, trim_state(patch.get('value')))
        elif op in ('add', 'replace'):
            target[key] = trim_state(patch.get('value'))
        else:
            raise ValueError('Unsupported state patch')
    return state


class Detector:
    def __init__(self, emit):
        self.emit = emit
        self.turn_status = {}

    def inspect(self, host, tid, state, baseline=False):
        title = state.get('title') or state.get('fallbackSearchTitle') or tid
        def event(key, kind, content):
            self.emit(host, tid, title, key, kind, content)
        detailed_wait = False
        for request in state.get('requests') or []:
            if request.get('completed'):
                continue
            detail = request_detail(request)
            if detail:
                detailed_wait = True
                event('request:' + str(request.get('id')), *detail)
        turns = turns_from(state)
        for t in turns:
            turn_id = t.get('turnId') or t.get('id')
            if not turn_id:
                continue
            answered = set()
            for item in t.get('items', []):
                if item.get('type') in ('userMessage', 'steeringUserMessage'):
                    content = item.get('content') or item.get('input') or []
                    for block in content if isinstance(content, list) else []:
                        text = block.get('text', '')
                        match = re.search(r'<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>', text, re.S)
                        if match:
                            try:
                                replies = json.loads(match.group(1))
                                if isinstance(replies, dict): replies = [replies]
                                answered.update(r.get('questionItemId') for r in replies)
                            except (ValueError, TypeError):
                                pass
            status = t.get('status')
            if status == 'inProgress':
                for item in t.get('items', []):
                    if item.get('type') == 'agentMessage':
                        for index, question in enumerate(item.get('questions') or []):
                            qid = json.dumps(['request_user_input_async', item.get('id'), index], separators=(',', ':'))
                            # Reply IDs can have spaces because different clients serialize differently.
                            normalized = {re.sub(r'\s+', '', str(a)) for a in answered}
                            if re.sub(r'\s+', '', qid) not in normalized:
                                detailed_wait = True
                                detail = question.get('title') or question.get('question') or ''
                                opts = question.get('options') or []
                                if opts:
                                    detail += '。可选：' + '；'.join(o.get('label', '') if isinstance(o, dict) else str(o) for o in opts)
                                event('question:' + str(item.get('id')) + ':' + str(index), '需要回答', detail)
                    elif item.get('type') == 'userInputResponse' and not item.get('completed'):
                        if not any(r.get('id') == item.get('requestId') for r in state.get('requests') or []):
                            detail = request_detail({'method': 'item/tool/requestUserInput', 'params': item})
                            if detail:
                                detailed_wait = True
                                event('request:' + str(item.get('requestId')), *detail)
            key = (host, tid, turn_id)
            previous = self.turn_status.get(key)
            self.turn_status[key] = status
            if baseline or status == previous:
                continue
            if status == 'failed':
                error = t.get('error') or {}
                content = error.get('message') if isinstance(error, dict) else str(error)
                event('turn:' + turn_id, '任务失败，需要查看', content or final_text(t) or '请打开任务查看失败原因。')
            elif status == 'completed' and final_text(t).strip():
                text = final_text(t)
                if not re.fullmatch(r'\s*(?:HEARTBEAT_OK|NO_REPLY|无变化[。.]?|没有变化[。.]?)\s*', text):
                    event('turn:' + turn_id, completion_kind(text), text)
        flags = (state.get('threadRuntimeStatus') or {}).get('activeFlags') or []
        if not detailed_wait and any(f in flags for f in ('waitingOnApproval', 'waitingOnUserInput')):
            # Give a detail-bearing request patch time to arrive first.
            pass


class Monitor:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        (DATA / 'controls').mkdir(exist_ok=True, mode=0o700)
        if not (DATA / 'settings.json').exists():
            write_json(DATA / 'settings.json', DEFAULTS)
        self.lock = (DATA / 'service.lock').open('w')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock.write(str(os.getpid())); self.lock.flush()
        handler = RotatingFileHandler(DATA / 'service.log', maxBytes=1000000, backupCount=2)
        logging.basicConfig(handlers=[handler], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        self.seen = read_json(DATA / 'seen.json', {})
        self.history = read_json(DATA / 'history.json', [])
        for old in self.history:
            if old.get('speech_status') in ('queued', 'speaking', 'generating'):
                old['speech_status'] = 'stopped'
                old['spoken'] = False
        if self.history:
            write_json(DATA / 'history.json', self.history)
        self.history_lock = threading.Lock()
        self.speeches = queue.PriorityQueue()
        self.sequence = 0
        self.states = {}
        self.revisions = {}
        self.subscribed = set()
        self.requested = {}
        self.catalog = {}
        self.last_catalog = 0
        self.socket = None
        self.client_id = None
        self.buffer = bytearray()
        self.running = True
        self.connection = '正在连接 Codex'
        self.warning = ''
        self.last_tick = 0
        self.last_event = None
        self.unread_count = 0
        self.start_ms = int(time.time() * 1000)
        self.db_cursor = self.start_ms
        self.detector = Detector(self.emit)
        self.speech_process = None
        self.current_entry = None
        self.speech_failure = ''
        self.stop_requested = threading.Event()
        self.wait_started = {}
        threading.Thread(target=self.speak_worker, daemon=True).start()

    def emit(self, host, tid, title, key, kind, content, force=False):
        if not clean(content):
            content = '请打开任务查看具体内容。'
        identity = hashlib.sha256((host + '|' + tid + '|' + key).encode()).hexdigest()
        if not force and identity in self.seen:
            return
        cfg = settings()
        if kind == '结果待查看' and not cfg.get('announce_completed', True):
            self.seen[identity] = time.time()
            write_json(DATA / 'seen.json', self.seen)
            return
        self.seen[identity] = time.time()
        if len(self.seen) > 10000:
            self.seen = dict(sorted(self.seen.items(), key=lambda p: p[1])[-8000:])
        write_json(DATA / 'seen.json', self.seen)
        entry = {'id': str(uuid.uuid4()), 'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                 'host': host, 'thread_id': tid, 'title': clean(title), 'kind': kind,
                 'content': clean(content), 'speech': speech_text(title, kind, content, int(cfg.get('speech_limit', 500)), cfg.get('speech_style', 'formal')),
                 'speech_source': clean(content, preserve_lines=True),
                 'speech_segments': speech_plan(title, kind, content, int(cfg.get('speech_limit', 500)), cfg.get('speech_style', 'formal')),
                 'spoken': False, 'speech_status': 'paused' if cfg.get('paused') else 'queued'}
        with self.history_lock:
            self.history.insert(0, entry)
            self.history = self.history[:200]
            write_json(DATA / 'history.json', self.history)
        self.last_event = entry['time']
        if not cfg.get('paused'):
            self.sequence += 1
            priority = 2 if kind == '结果待查看' else 0
            self.speeches.put((priority, self.sequence, entry))

    def mark_speech(self, entry, status, error=''):
        with self.history_lock:
            entry['speech_status'] = status
            entry['spoken'] = status == 'spoken'
            if error: entry['speech_error'] = error
            write_json(DATA / 'history.json', self.history)

    def speak_worker(self):
        while self.running:
            try:
                _, _, entry = self.speeches.get(timeout=1)
            except queue.Empty:
                continue
            cfg = settings()
            if cfg.get('paused'):
                self.mark_speech(entry, 'paused')
                self.speeches.task_done()
                continue
            self.current_entry = entry
            self.mark_speech(entry, 'generating' if str(cfg.get('voice_id', '')).startswith('local:') else 'speaking')
            self.stop_requested.clear()
            try:
                # Input is stdin; it is never interpolated into shell code.
                command, body = speech_job(cfg, entry)
                if not all(Path(p).exists() for p in command):
                    raise RuntimeError('语音组件缺失，请重新安装播报程序')
                payload = json.dumps(body, ensure_ascii=False).encode()
                self.speech_process = subprocess.Popen(command,
                                                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                _, err = self.speech_process.communicate(payload, timeout=360)
                if self.stop_requested.is_set():
                    self.mark_speech(entry, 'paused' if settings().get('paused') else 'stopped')
                    continue
                if self.speech_process.returncode != 0:
                    detail = err.decode(errors='replace').strip().splitlines()
                    raise RuntimeError(detail[-1][-300:] if detail else '语音进程退出')
                self.mark_speech(entry, 'spoken')
                self.speech_failure = ''
            except Exception as exc:
                if self.speech_process and self.speech_process.poll() is None:
                    self.speech_process.kill()
                msg = str(exc)[:250]
                self.mark_speech(entry, 'failed', msg)
                self.speech_failure = '语音输出失败：' + msg
                logging.error('Speech failed: %s', msg)
            finally:
                self.speech_process = None
                self.current_entry = None
                self.speeches.task_done()

    def send(self, message):
        body = json.dumps(message, ensure_ascii=False).encode()
        self.socket.sendall(struct.pack('<I', len(body)) + body)

    def broadcast(self, method, params):
        self.send({'type': 'broadcast', 'method': method, 'version': 1,
                   'sourceClientId': self.client_id, 'params': params})

    def subscribe(self, host, tid, force=False):
        key = (host, tid)
        if not self.client_id or (key in self.subscribed and not force):
            return
        self.broadcast('thread-stream-following-changed', {'hostId': host, 'conversationId': tid, 'following': True})
        self.subscribed.add(key)
        self.requested[key] = time.monotonic()

    def discover(self):
        try:
            path = CODEX / 'sqlite/codex-dev.db'
            with sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=2) as db:
                rows = db.execute("SELECT host_id,thread_id,display_title FROM local_thread_catalog WHERE host_id NOT LIKE 'chatgpt%' AND missing_candidate=0").fetchall()
            with sqlite3.connect('file:%s?mode=ro' % (CODEX / 'state_5.sqlite'), uri=True, timeout=2) as db:
                archived = {r[0] for r in db.execute('SELECT id FROM threads WHERE archived=1')}
            self.catalog = {(h, t): name for h, t, name in rows if h != 'local' or t not in archived}
            for key in self.catalog:
                self.subscribe(*key)
            # Owners can become active later; request an initial snapshot again.
            now = time.monotonic()
            for key, since in list(self.requested.items()):
                if key not in self.states and now - since > 30:
                    self.subscribe(*key, force=True)
            for key in self.subscribed - self.catalog.keys():
                self.broadcast('thread-stream-following-changed', {'hostId': key[0], 'conversationId': key[1], 'following': False})
                self.subscribed.discard(key)
                self.states.pop(key, None)
                self.revisions.pop(key, None)
            self.warning = ''
        except (sqlite3.Error, OSError) as exc:
            self.warning = '任务目录读取失败：' + str(exc)[:160]
            logging.warning('%s', self.warning)
        self.last_catalog = time.monotonic()

    def db_fallback(self):
        """Recover newly finished local turns if their UI owner wasn't loaded."""
        newest = int(time.time() * 1000)
        try:
            with sqlite3.connect('file:%s?mode=ro' % (CODEX / 'thread_history_1.sqlite'), uri=True, timeout=2) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT thread_id,turn_id,status,error_json,completed_at,final_agent_item_id FROM thread_turns WHERE completed_at>=? AND status IN ('completed','failed')", (self.db_cursor // 1000 - 2,)).fetchall()
                for row in rows:
                    completed = row['completed_at'] or 0
                    completed_ms = completed if completed > 100000000000 else completed * 1000
                    if completed_ms < self.start_ms - 1000:
                        continue
                    tid = row['thread_id']
                    if ('local', tid) not in self.catalog:
                        continue
                    key = ('local', tid)
                    items = db.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type='agentMessage' ORDER BY rollout_ordinal", (tid, row['turn_id'])).fetchall()
                    turn = {'turnId': row['turn_id'], 'status': row['status'], 'items': [json.loads(i[0]) for i in items], 'error': json.loads(row['error_json']) if row['error_json'] else None}
                    text = final_text(turn)
                    if row['status'] == 'failed':
                        err = turn.get('error') or {}
                        detail = err.get('message') if isinstance(err, dict) else str(err)
                        self.emit('local', tid, self.catalog[key], 'turn:' + row['turn_id'], '任务失败，需要查看', detail or text or '请打开任务查看失败原因。')
                    elif text.strip() and not re.fullmatch(r'\s*(?:HEARTBEAT_OK|NO_REPLY|无变化[。.]?|没有变化[。.]?)\s*', text):
                        self.emit('local', tid, self.catalog[key], 'turn:' + row['turn_id'], completion_kind(text), text)
            self.db_cursor = newest
        except (sqlite3.Error, OSError, ValueError) as exc:
            logging.warning('Completion fallback unavailable: %s', str(exc)[:150])

    def unread_scan(self):
        """Announce current unread results, even from before the service started.

        Uses the same turn key as live completion events to avoid double speech.
        Reading these files does not mark anything read or load a chat in the UI.
        """
        try:
            global_state = json.loads((CODEX / '.codex-global-state.json').read_text())
            tids = unread_local_ids(global_state)
            tids = {tid for tid in tids if ('local', tid) in self.catalog}
            self.unread_count = len(tids)
            if not tids:
                return
            with sqlite3.connect('file:%s?mode=ro' % (CODEX / 'thread_history_1.sqlite'), uri=True, timeout=2) as db:
                for tid in sorted(tids):
                    row = db.execute("SELECT turn_id,status,error_json,final_agent_item_id FROM thread_turns WHERE thread_id=? AND status IN ('completed','failed') ORDER BY rollout_ordinal DESC LIMIT 1", (tid,)).fetchone()
                    if not row:
                        continue
                    turn_id, status, error_json, final_id = row
                    # Prefer the explicitly recorded final item over commentary.
                    final = db.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? AND item_id=?", (tid, turn_id, final_id)).fetchone() if final_id else None
                    if final:
                        text = json.loads(final[0]).get('text', '')
                    else:
                        items = db.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type='agentMessage' ORDER BY rollout_ordinal", (tid, turn_id)).fetchall()
                        text = final_text({'items': [json.loads(i[0]) for i in items]})
                    if status == 'failed':
                        error = json.loads(error_json) if error_json else {}
                        text = (error.get('message') if isinstance(error, dict) else str(error)) or text or '请打开任务查看失败原因。'
                        kind = '任务失败，需要查看'
                    else:
                        kind = '待查看（未读）'
                        text = text or 'Codex 将此任务标为未读，请打开任务查看结果。'
                    self.emit('local', tid, self.catalog[('local', tid)], 'turn:' + turn_id, kind, text)
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            logging.warning('Unread-state scan unavailable: %s', str(exc)[:180])

    def handle(self, message):
        typ = message.get('type')
        if typ == 'client-discovery-request':
            self.send({'type': 'client-discovery-response', 'requestId': message['requestId'], 'response': {'canHandle': False}})
            return
        if typ == 'request':
            self.send({'type': 'response', 'requestId': message['requestId'], 'resultType': 'error', 'error': 'read-only-monitor'})
            return
        if typ == 'response' and message.get('method') == 'initialize':
            if message.get('resultType') != 'success':
                raise ValueError('Codex 接口初始化失败')
            self.client_id = message['result']['clientId']
            self.connection = '已连接 Codex 本机接口'
            self.discover()
            return
        if typ != 'broadcast':
            return
        method = message.get('method')
        p = message.get('params') or {}
        if method == 'thread-stream-following-status-requested':
            key = (p.get('hostId', 'local'), p.get('conversationId'))
            if key in self.subscribed:
                self.broadcast('thread-stream-following-changed', {'hostId': key[0], 'conversationId': key[1], 'following': True})
        elif method in ('thread-read-state-changed', 'thread-stream-following-changed'):
            tid = p.get('conversationId') or p.get('threadId')
            if tid and message.get('sourceClientId') != self.client_id:
                self.subscribe(p.get('hostId', 'local'), tid)
        elif method == 'thread-stream-state-changed':
            if message.get('version') != 11:
                self.warning = 'Codex 接口版本变化，实时事件需要适配；本机完成事件仍使用只读记录补充。'
                return
            host, tid = p.get('hostId', 'local'), p.get('conversationId')
            key = (host, tid)
            change = p.get('change') or {}
            if change.get('type') == 'snapshot':
                # Baseline old results, but notify currently pending requests.
                initial = key not in self.states
                self.states[key] = trim_state(change['conversationState'])
                self.revisions[key] = change.get('revision')
                self.requested.pop(key, None)
                self.detector.inspect(host, tid, self.states[key], baseline=initial)
            elif change.get('type') == 'patches':
                if key not in self.states or self.revisions.get(key) != change.get('baseRevision'):
                    self.states.pop(key, None)
                    self.subscribe(host, tid, force=True)
                    return
                try:
                    self.states[key] = apply_patches(self.states[key], change.get('patches') or [])
                except (KeyError, IndexError, TypeError, ValueError):
                    self.states.pop(key, None)
                    self.subscribe(host, tid, force=True)
                    return
                self.revisions[key] = change.get('revision')
                self.detector.inspect(host, tid, self.states[key])

    def controls(self):
        for path in sorted((DATA / 'controls').glob('*.json')):
            command = read_json(path, {})
            path.unlink(missing_ok=True)
            action = command.get('action')
            if action == 'test':
                self.emit('test', '', '语音播报测试', str(uuid.uuid4()), '测试提醒', '当前使用清晰、正式的中文女声。\n我会先读出任务名称，再说明需要您处理的内容。\n例如：费用核对已经完成。请确认成本数据，再继续下一步。', force=True)
            elif action == 'replay':
                with self.history_lock:
                    old = next((dict(e) for e in self.history if e['id'] == command.get('id')), None)
                if old:
                    self.emit(old['host'], old['thread_id'], old['title'], str(uuid.uuid4()), old['kind'], old.get('speech_source', old['content']), force=True)
            elif action == 'stop_speech':
                self.stop_requested.set()
                if self.speech_process and self.speech_process.poll() is None:
                    self.speech_process.terminate()
                while True:
                    try:
                        _, _, entry = self.speeches.get_nowait()
                        self.mark_speech(entry, 'stopped')
                        self.speeches.task_done()
                    except queue.Empty:
                        break
        if settings().get('paused') and self.speech_process and self.speech_process.poll() is None:
            self.stop_requested.set()
            self.speech_process.terminate()

    def status(self):
        cfg = settings()
        proc, entry = self.speech_process, self.current_entry
        if proc is not None and entry is not None and entry.get('speech_status') == 'generating':
            progress = read_json(DATA / 'neural-progress.json', {})
            if progress.get('pid') == proc.pid and progress.get('phase') == 'playing':
                self.mark_speech(entry, 'speaking')
        # Runtime flags are a safety net when the app has no detail-bearing request.
        for (host, tid), state in self.states.items():
            flags = (state.get('threadRuntimeStatus') or {}).get('activeFlags') or []
            wait_flag = next((f for f in flags if f in ('waitingOnApproval', 'waitingOnUserInput')), None)
            key = (host, tid)
            if not wait_flag:
                self.wait_started.pop(key, None)
                continue
            since = self.wait_started.setdefault(key, time.monotonic())
            items = all_items(state)
            if state.get('requests') or any(i.get('questions') or (i.get('type') == 'userInputResponse' and not i.get('completed')) for i in items):
                continue
            if time.monotonic() - since > 3:
                turns = turns_from(state)
                current = turns[-1] if turns else {}
                detail = final_text(current) or 'Codex 正在等待人工处理，具体操作请打开任务查看。'
                self.emit(host, tid, state.get('title') or self.catalog.get(key, tid), 'wait:' + str(current.get('turnId') or since) + ':' + wait_flag,
                          '等待你确认' if wait_flag == 'waitingOnApproval' else '需要回答', detail)
        with self.history_lock:
            latest = {h + '|' + t: {'title': s.get('title') or self.catalog.get((h, t), t),
                         'runtime': s.get('threadRuntimeStatus'), 'pending_requests': len(s.get('requests') or [])}
                      for (h, t), s in self.states.items()}
        write_json(DATA / 'status.json', {'pid': os.getpid(), 'heartbeat': time.time(), 'connection': self.connection,
                   'paused': cfg['paused'], 'catalog_count': len(self.catalog), 'live_count': len(self.states),
                   'unread_count': self.unread_count,
                   'warning': self.warning or self.speech_failure, 'queue_count': self.speeches.qsize(),
                   'last_event': self.last_event, 'live_tasks': latest})

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(str(CODEX / 'ipc/ipc.sock'))
        self.socket = sock
        self.buffer.clear()
        self.client_id = None
        self.states.clear()
        self.revisions.clear()
        self.subscribed.clear()
        self.requested.clear()
        self.send({'type': 'request', 'requestId': str(uuid.uuid4()), 'sourceClientId': 'initializing-client',
                   'version': 0, 'method': 'initialize', 'params': {'clientType': 'voice-monitor'}})
        self.connection = '正在初始化 Codex 接口'

    def run(self):
        retry_at = 0
        while self.running:
            try:
                self.controls()
                now = time.monotonic()
                if self.socket is None and now >= retry_at:
                    self.connect()
                if self.socket is not None:
                    readable, _, _ = select.select([self.socket], [], [], 0.3)
                    if readable:
                        chunk = self.socket.recv(1024 * 1024)
                        if not chunk:
                            raise ConnectionError('Codex 接口已断开')
                        self.buffer.extend(chunk)
                        while len(self.buffer) >= 4:
                            length = struct.unpack_from('<I', self.buffer)[0]
                            if not 0 < length <= MAX_FRAME:
                                raise ValueError('Codex 接口消息格式发生变化')
                            if len(self.buffer) < length + 4:
                                break
                            body = bytes(self.buffer[4:length + 4])
                            del self.buffer[:length + 4]
                            self.handle(json.loads(body))
                else:
                    time.sleep(0.3)
                if self.client_id and now - self.last_catalog > 15:
                    self.discover()
                if now - self.last_tick > 2:
                    self.unread_scan()
                    self.db_fallback()
                    self.status()
                    self.last_tick = now
            except (OSError, ValueError, ConnectionError) as exc:
                if self.socket:
                    self.socket.close()
                self.socket = None
                self.client_id = None
                self.connection = '等待 Codex 启动／重新连接'
                if not isinstance(exc, (FileNotFoundError, ConnectionRefusedError)):
                    logging.warning('Connection retry: %s', str(exc)[:160])
                retry_at = time.monotonic() + 3
                self.status()
        if self.socket:
            self.socket.close()
        if self.speech_process and self.speech_process.poll() is None:
            self.speech_process.terminate()


if __name__ == '__main__':
    os.umask(0o077)
    monitor = Monitor()
    def stop(signum, frame):
        monitor.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    monitor.run()
