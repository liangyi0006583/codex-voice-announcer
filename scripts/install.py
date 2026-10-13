#!/usr/bin/env python3
"""Install built sources; staging with --home --no-start never starts a monitor."""
import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from build import APP_NAME, SERVICE, ROOT, build_app


def install(home, start=True, python=None):
    home=Path(home).resolve()
    if start and home != Path.home().resolve():
        raise ValueError('An alternate --home requires --no-start.')
    interpreter=Path(python or sys.executable).resolve()
    if not interpreter.is_file():
        raise ValueError('Python interpreter does not exist.')
    runtime=home/'Library/Application Support/CodexVoiceAnnouncer'
    controls=runtime/'controls'
    agents=home/'Library/LaunchAgents'
    for directory in [runtime,controls,agents,home/'Desktop']:
        directory.mkdir(parents=True,exist_ok=True)
    runtime.chmod(0o700); controls.chmod(0o700)
    target=f'gui/{os.getuid()}/{SERVICE}'
    if start:
        subprocess.run(['launchctl','bootout',target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='codex-voice-build-') as folder:
        app=build_app(Path(folder)/APP_NAME)
        destination=home/'Desktop'/APP_NAME
        if destination.is_symlink():
            raise ValueError('Refusing to replace a symbolic-link app destination.')
        if destination.exists():
            backup=destination.with_name(destination.name+'.previous-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            destination.rename(backup)
        shutil.copytree(app,destination)
        for name in ['monitor.py','NeuralEngine.py','SpeechEngine','AudioPlayer']:
            shutil.copy2(app/'Contents/Resources'/name,runtime/name); (runtime/name).chmod(0o700)
    settings=runtime/'settings.json'
    if not settings.exists():
        settings.write_text(json.dumps({'paused':False,'announce_completed':True,'rate':190,
            'pitch_semitones':0,'speech_style':'formal','speech_limit':500,
            'voice_id':'com.apple.voice.compact.zh-CN.Tingting'},ensure_ascii=False))
        settings.chmod(0o600)
    plist=agents/(SERVICE+'.plist')
    metadata={'Label':SERVICE,'ProgramArguments':[str(interpreter),str(runtime/'monitor.py')],
        'RunAtLoad':True,'KeepAlive':True,'ProcessType':'Background','ThrottleInterval':5,
        'Umask':63,'EnvironmentVariables':{'PYTHONUNBUFFERED':'1'},
        'StandardOutPath':str(runtime/'launch.log'),'StandardErrorPath':str(runtime/'launch.log')}
    with plist.open('wb') as f: plistlib.dump(metadata,f)
    plist.chmod(0o600)
    if start:
        subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],check=True)
    return destination


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,default=Path.home())
    p.add_argument('--no-start',action='store_true')
    p.add_argument('--python',help='Stable Python 3 interpreter for the background service')
    args=p.parse_args()
    os.umask(0o077)
    try: print(install(args.home,not args.no_start,args.python))
    except (OSError,RuntimeError,ValueError,subprocess.CalledProcessError) as e:
        p.exit(1,f'Install failed: {e}\n')


if __name__=='__main__': main()
