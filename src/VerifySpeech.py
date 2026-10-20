#!/usr/bin/env python3
"""Offline, unprompted ASR gate for language, content drift and generated loops."""
import os
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
import json
import math
from pathlib import Path
import re
import sys
import wave
from difflib import SequenceMatcher
import numpy as np
from scipy.signal import resample_poly
import mlx_whisper
from opencc import OpenCC
from SpeechText import normalize_content

ROOT = Path(os.environ.get('CODEX_VOICE_DATA_DIR', str(Path.home() / 'Library/Application Support/CodexVoiceAnnouncer')))
TO_SIMPLIFIED = OpenCC('t2s')


def verify(items):
    if not items:
        raise ValueError('没有可校验的语音片段')
    model = ROOT / 'models/whisper-small-mlx'
    if not (model / 'weights.npz').exists():
        raise ValueError('本地中文校验模型缺失')
    results = []
    for item in items:
        with wave.open(item['file'], 'rb') as f:
            sr = f.getframerate()
            audio = np.frombuffer(f.readframes(f.getnframes()), '<i2').astype(np.float32) / 32768
        divisor = math.gcd(sr, 16000)
        audio = resample_poly(audio, 16000 // divisor, sr // divisor)
        # Do not force ASR to Chinese or supply the target as a prompt: that would
        # conceal foreign-language output and make the check self-fulfilling.
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=str(model), language=None,
            condition_on_previous_text=False, verbose=None, temperature=0.0)
        actual = normalize_content(TO_SIMPLIFIED.convert(result['text']))
        expected = normalize_content(TO_SIMPLIFIED.convert(item['text']))
        ratio = SequenceMatcher(None, expected, actual, autojunk=False).ratio()
        repeated = any(actual.count(m.group(1)) > expected.count(m.group(1)) + 1
                       for m in re.finditer(r'(.{6,40})\1{2,}', actual))
        # Very short labels can be ambiguous to a language detector; meaningful
        # Chinese batches must independently be recognized as Chinese.
        han_count = len(re.findall(r'[\u4e00-\u9fff]', item['text']))
        language_ok = result['language'] == 'zh' or han_count < 8
        passed = language_ok and ratio >= 0.80 and not repeated
        results.append({'language': result['language'], 'similarity': round(ratio, 3),
                        'repeated': repeated, 'passed': passed})
    return {'passed': all(row['passed'] for row in results), 'chunks': results}


if __name__ == '__main__':
    try:
        value = verify(json.load(sys.stdin)['items'])
        print(json.dumps(value), flush=True)
        sys.exit(0 if value['passed'] else 4)
    except Exception as exc:
        print('中文朗读校验失败：' + str(exc), file=sys.stderr, flush=True)
        sys.exit(4)
