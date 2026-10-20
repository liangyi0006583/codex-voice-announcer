#!/usr/bin/env python3
"""Download optional open model weights; no task text or audio is uploaded."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv
from build import ROOT

MODEL_REPO='mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit'
MODEL_REVISION='e7dd0585652209fa0d7783659aad4e8a324de11c'
ASR_REPO='mlx-community/whisper-small-mlx'
ASR_REVISION='45f3915923c7a79a5a5b5a7d909d39aeb0e5630e'


def setup(data):
    if sys.platform!='darwin' or platform.machine()!='arm64':
        raise RuntimeError('Optional neural speech requires Apple Silicon macOS.')
    if sys.version_info < (3,12):
        raise RuntimeError('Run this optional setup with Python 3.12 or later.')
    data=Path(data); data.mkdir(parents=True,exist_ok=True); data.chmod(0o700)
    environment=data/'neural-venv'
    venv.EnvBuilder(with_pip=True,symlinks=False).create(environment)
    python=environment/'bin/python'
    subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements-neural.txt')],check=True)
    # Fixed public model snapshot, unauthenticated inbound download only.
    download="""import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id=sys.argv[1],revision=sys.argv[2],local_dir=sys.argv[3],token=False)
"""
    env=dict(os.environ,HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
    subprocess.run([str(python),'-c',download,MODEL_REPO,MODEL_REVISION,
                    str(data/'models/Qwen3-TTS-1.7B-Base-8bit')],env=env,check=True)
    asr_download="""import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id=sys.argv[1],revision=sys.argv[2],local_dir=sys.argv[3],token=False,
                  allow_patterns=['config.json','weights.npz'])
"""
    subprocess.run([str(python),'-c',asr_download,ASR_REPO,ASR_REVISION,
                    str(data/'models/whisper-small-mlx')],env=env,check=True)
    print('TTS and local Chinese-verification models installed. Select a bundled voice or import your own.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-directory',type=Path,
                   default=Path.home()/'Library/Application Support/CodexVoiceAnnouncer')
    args=p.parse_args(); os.umask(0o077)
    try: setup(args.data_directory)
    except (OSError,RuntimeError,subprocess.CalledProcessError) as e:
        p.exit(1,f'Optional setup failed: {e}\n')
