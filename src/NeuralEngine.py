#!/usr/bin/env python3
"""Offline reference TTS, fully buffered before route-aware native playback."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

import contextlib
import gc
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import wave

import mlx.core as mx
import numpy as np
from mlx_audio.tts.utils import load_model

ROOT = Path.home() / 'Library/Application Support/CodexVoiceAnnouncer'
MODEL = ROOT / 'models/Qwen3-TTS-1.7B-Base-8bit'
REFERENCES = {'local:custom-1': 'custom-1', 'local:custom-2': 'custom-2'}
playback_process = None


def stop(signum, _frame):
    # The monitor stops this parent; never leave the native player speaking alone.
    if playback_process is not None and playback_process.poll() is None:
        playback_process.terminate()
        try:
            playback_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            playback_process.kill(); playback_process.wait()
    raise SystemExit(128 + signum)


def shape_pauses(audio, sr, style):
    """Adjust only existing quiet gaps of at least 240 ms, never voiced syllables."""
    factor = 1.12 if style == 'gentle' else (0.85 if style == 'firm' else 1.0)
    if factor == 1.0:
        return audio
    width = max(1, int(sr * 0.02))
    total = len(audio) // width
    if not total:
        return audio
    levels = np.sqrt(np.mean(audio[:total * width].reshape(total, width) ** 2, axis=1))
    quiet = levels < 0.001
    edges = np.diff(np.r_[False, quiet, False].astype(np.int8))
    parts = []; last = 0
    for first, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
        a, b = int(first * width), int(end * width)
        if b - a < sr * 0.24 or a == 0 or b >= total * width:
            continue
        # Keep both edges intact. Only add/remove samples at the quiet midpoint.
        middle = (a + b) // 2
        amount = int((b - a) * abs(factor - 1))
        parts.append(audio[last:middle])
        if factor > 1:
            parts.append(np.zeros(amount, dtype=np.float32)); last = middle
        else:
            last = middle + amount
    parts.append(audio[last:])
    return np.concatenate(parts)


def write_wave(path, audio, sr):
    audio = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError('语音模型未生成有效音频')
    peak = float(np.max(np.abs(audio)))
    if peak < 0.0001:
        raise ValueError('语音模型生成了静音')
    if peak > 0.98:
        audio *= 0.98 / peak
    fade = min(len(audio) // 2, int(sr * 0.008))
    if fade:
        audio[:fade] *= np.linspace(0, 1, fade)
        audio[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())


def run(payload):
    global playback_process
    key = payload.get('voiceId', 'local:custom-1')
    if key not in REFERENCES:
        raise ValueError('未配置此角色的参考音频')
    reference = ROOT / 'reference' / REFERENCES[key]
    source = json.loads((reference / 'source.json').read_text())
    ref_path = reference / 'reference.wav'
    if not ref_path.exists() or not (MODEL / 'model.safetensors').exists():
        raise ValueError('本地模型或角色参考音频缺失')
    text = payload.get('text') or '\n'.join(s['text'] for s in payload.get('segments', []))
    if not text.strip():
        raise ValueError('播报内容为空')
    progress_path = payload.get('progressPath')
    def progress(phase):
        if progress_path:
            p = Path(progress_path)
            temp = p.with_suffix('.tmp-' + str(os.getpid()))
            temp.write_text(json.dumps({'pid': os.getpid(), 'phase': phase, 'updated': time.time()}))
            temp.chmod(0o600); temp.replace(p)
    progress('loading')
    start = time.monotonic()
    mx.random.seed(42)
    mx.set_cache_limit(512 * 1024 * 1024)
    with contextlib.redirect_stdout(sys.stderr):
        model = load_model(MODEL)
    progress('generating')
    chunks = []
    sr = int(model.sample_rate)
    with contextlib.redirect_stdout(sys.stderr):
        for result in model.generate(text=text, ref_audio=str(ref_path), ref_text=source['reference_text'],
                                     lang_code=payload.get('language', 'Chinese'), temperature=0.75,
                                     top_p=0.92, max_tokens=2400, verbose=False, stream=False):
            audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
            if len(audio):
                if not np.isfinite(audio).all():
                    raise ValueError('合成音频包含无效采样')
                chunks.append(audio)
    if not chunks:
        raise ValueError('模型未返回音频')
    style = payload.get('style', 'formal')
    # Pick up a style change made while the model was generating.
    if payload.get('settingsPath'):
        try:
            style = json.loads(Path(payload['settingsPath']).read_text()).get('speech_style', style)
        except (OSError, ValueError):
            pass
    combined = shape_pauses(np.concatenate(chunks), sr, style)
    duration = len(combined) / sr
    generation_seconds = time.monotonic() - start
    peak_memory = mx.get_peak_memory() / 1e9
    del model, result, audio, chunks
    gc.collect(); mx.clear_cache()
    player = ROOT / 'AudioPlayer'
    if not player.exists():
        raise ValueError('原生音频播放器缺失')
    with tempfile.TemporaryDirectory(prefix='codex-voice-') as directory:
        raw = Path(directory) / 'voice.wav'
        write_wave(raw, combined, sr)
        plan = {'file': str(raw), 'rate': payload.get('rate', 190),
                'pitchSemitones': payload.get('pitchSemitones', 0), 'style': style,
                'settingsPath': payload.get('settingsPath'), 'progressPath': progress_path}
        if payload.get('output'):
            plan['output'] = payload['output']
        else:
            progress('playing')
        playback_process = subprocess.Popen([str(player)], stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _, error = playback_process.communicate(json.dumps(plan).encode(), timeout=300)
        if playback_process.returncode:
            raise RuntimeError(error.decode(errors='replace').strip()[-400:] or '原生播放器退出')
        playback_process = None
    progress('complete')
    print(json.dumps({'voice': key, 'sample_rate': sr, 'duration_before_effects': duration,
                      'generation_seconds': round(generation_seconds, 2),
                      'peak_memory_gb': round(peak_memory, 2), 'buffered': True,
                      'offline': True}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        run(json.load(sys.stdin))
    except Exception as exc:
        if playback_process is not None and playback_process.poll() is None:
            playback_process.kill(); playback_process.wait()
        print('角色语音合成失败：' + str(exc), file=sys.stderr, flush=True)
        sys.exit(2)
