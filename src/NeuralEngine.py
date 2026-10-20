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
from SpeechText import speech_batches

ROOT = Path(os.environ.get('CODEX_VOICE_DATA_DIR', str(Path.home() / 'Library/Application Support/CodexVoiceAnnouncer')))
MODEL = ROOT / 'models/Qwen3-TTS-1.7B-Base-8bit'
REFERENCES = {'local:warden': 'warden', 'local:red-queen': 'red-queen', 'local:aizen': 'aizen',
              'local:custom-1': 'custom-1', 'local:custom-2': 'custom-2', 'local:custom-3': 'custom-3'}
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


def limit_long_pauses(audio, sr):
    """Remove only the middle of existing long quiet gaps, preserving both edges."""
    width = max(1, int(sr * .02)); count = len(audio) // width
    if not count: return audio
    quiet = np.sqrt(np.mean(audio[:count * width].reshape(count, width) ** 2, axis=1)) < .001
    edges = np.diff(np.r_[False, quiet, False].astype(np.int8))
    pieces = []; last = 0
    for first, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
        start, stop = int(first * width), int(end * width)
        if start == 0 or stop >= count * width or stop - start <= sr * .75: continue
        cut_start = start + int(sr * .28)
        cut_stop = stop - int(sr * .28)
        pieces.append(audio[last:cut_start]); last = cut_stop
    pieces.append(audio[last:])
    return np.concatenate(pieces)


def run(payload):
    global playback_process
    key = payload.get('voiceId', 'local:warden')
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
    texts = speech_batches(text)
    sr = int(model.sample_rate)
    language = str(payload.get('language', 'Chinese')).lower()
    if language not in model.get_supported_languages():
        raise ValueError('不支持的播报语言：' + language)
    reference_language = str(source.get('reference_language', source.get('language', ''))).lower()
    # Cross-language cloning must not carry foreign reference phonemes into Mandarin.
    speaker_only = (source.get('clone_mode') == 'speaker_only' or
                    (language == 'chinese' and reference_language not in ('chinese', 'zh')))
    reference_text = None if speaker_only else source['reference_text']
    for part in texts:
        with contextlib.redirect_stdout(sys.stderr):
            for result in model.generate(text=part, ref_audio=str(ref_path), ref_text=reference_text,
                                         lang_code=language, temperature=0.45 if speaker_only else 0.75,
                                         top_p=0.85 if speaker_only else 0.92, repetition_penalty=1.1,
                                         max_tokens=800, verbose=False, stream=False):
                audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                if not len(audio) or not np.isfinite(audio).all():
                    raise ValueError('合成音频包含无效采样')
                if len(audio) / sr > max(14, len(part) * 0.65):
                    raise ValueError('语音时长异常，可能出现循环朗读')
                # Keep only a small, consistent edge pause between complete batches.
                active = np.flatnonzero(np.abs(audio) > 0.002)
                if not len(active):
                    raise ValueError('语音模型生成了静音')
                audio = audio[max(0, active[0] - int(sr * .08)):
                              min(len(audio), active[-1] + int(sr * .16))]
                chunks.append(audio.copy())
        # Release each batch's KV buffers before generating the next one.
        gc.collect(); mx.clear_cache()
    if not chunks:
        raise ValueError('模型未返回音频')
    style = payload.get('style', 'formal')
    # Pick up a style change made while the model was generating.
    if payload.get('settingsPath'):
        try:
            style = json.loads(Path(payload['settingsPath']).read_text()).get('speech_style', style)
        except (OSError, ValueError):
            pass
    combined = limit_long_pauses(shape_pauses(np.concatenate(chunks), sr, style), sr)
    duration = len(combined) / sr
    generation_seconds = time.monotonic() - start
    peak_memory = mx.get_peak_memory() / 1e9
    del model, result, audio
    gc.collect(); mx.clear_cache()
    player = ROOT / 'AudioPlayer'
    if not player.exists():
        raise ValueError('原生音频播放器缺失')
    with tempfile.TemporaryDirectory(prefix='codex-voice-') as directory:
        raw = Path(directory) / 'voice.wav'
        write_wave(raw, combined, sr)
        items = []
        for index, (chunk, part) in enumerate(zip(chunks, texts)):
            candidate = Path(directory) / ('chunk-' + str(index) + '.wav')
            write_wave(candidate, chunk, sr)
            items.append({'file': str(candidate), 'text': part})
        del chunks
        progress('validating')
        playback_process = subprocess.Popen([sys.executable, str(ROOT / 'VerifySpeech.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        checked, error = playback_process.communicate(json.dumps({'items': items}).encode(), timeout=180)
        if playback_process.returncode:
            raise ValueError('角色语音未通过中文内容校验')
        validation = json.loads(checked)
        playback_process = None
        plan = {'file': str(raw), 'rate': payload.get('rate', 190),
                'pitchSemitones': payload.get('pitchSemitones', 0), 'style': style,
                'settingsPath': payload.get('settingsPath'), 'progressPath': progress_path}
        if payload.get('output'):
            plan['output'] = payload['output']
        else:
            progress('playing')
        playback_process = subprocess.Popen([str(player)], stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _, error = playback_process.communicate(json.dumps(plan).encode(), timeout=max(120, duration / 0.6 + 30))
        if playback_process.returncode:
            raise RuntimeError(error.decode(errors='replace').strip()[-400:] or '原生播放器退出')
        playback_process = None
    progress('complete')
    print(json.dumps({'voice': key, 'sample_rate': sr, 'duration_before_effects': duration,
                      'generation_seconds': round(generation_seconds, 2),
                      'peak_memory_gb': round(peak_memory, 2), 'buffered': True,
                      'offline': True, 'language': language,
                      'clone_mode': 'speaker_only' if speaker_only else 'icl',
                      'validation': validation, 'batch_count': len(texts)}, ensure_ascii=False), flush=True)


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
