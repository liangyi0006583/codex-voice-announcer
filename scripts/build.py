#!/usr/bin/env python3
"""Compile an unsigned macOS app from the public sources."""
import argparse
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'Codex Voice Announcer.app'
SERVICE = 'io.github.codex-voice-announcer.monitor'
BUNDLE_ID = 'io.github.codex-voice-announcer.control'


def build_app(destination):
    if sys.platform != 'darwin':
        raise RuntimeError('Building requires macOS and Xcode Command Line Tools.')
    compiler = subprocess.run(['xcrun', '--find', 'swiftc'], capture_output=True, text=True, check=True).stdout.strip()
    sdk = subprocess.run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], capture_output=True, text=True, check=True).stdout.strip()
    app = Path(destination)
    resources = app/'Contents/Resources'
    executable = app/'Contents/MacOS'
    resources.mkdir(parents=True, exist_ok=True); executable.mkdir(parents=True, exist_ok=True)
    for source, output in [('Main.swift',executable/'CodexVoice'),
                           ('SpeechEngine.swift',resources/'SpeechEngine'),
                           ('AudioPlayer.swift',resources/'AudioPlayer')]:
        subprocess.run([compiler, '-sdk', sdk, '-target', platform.machine()+'-apple-macosx13.0',
                        '-O', str(ROOT/'src'/source), '-o', str(output)], check=True)
    for name in ['monitor.py','NeuralEngine.py','SpeechText.py','VerifySpeech.py']:
        shutil.copy2(ROOT/'src'/name,resources/name)
    shutil.copytree(ROOT/'voices',resources/'voices',dirs_exist_ok=True)
    shutil.copy2(ROOT/'docs/USAGE.md',resources/'使用说明.txt')
    metadata = {'CFBundleName':'Codex Voice Announcer','CFBundleDisplayName':'Codex 语音播报',
                'CFBundleExecutable':'CodexVoice','CFBundleIdentifier':BUNDLE_ID,
                'CFBundlePackageType':'APPL','CFBundleShortVersionString':'1.1.0',
                'CFBundleVersion':'2','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True}
    with (app/'Contents/Info.plist').open('wb') as f:
        plistlib.dump(metadata,f)
    return app


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'.build'/APP_NAME)
    args=p.parse_args()
    try:
        print(build_app(args.output))
    except (OSError,RuntimeError,subprocess.CalledProcessError) as e:
        p.exit(1, f'Build failed: {e}\n')


if __name__=='__main__':
    main()
