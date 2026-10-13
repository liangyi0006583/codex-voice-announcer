#!/usr/bin/env python3
"""Stop the installed service; retain all personal settings and voice data."""
import argparse
import datetime
import os
from pathlib import Path
import subprocess
from build import APP_NAME, SERVICE


def uninstall(home,stop=True):
    home=Path(home).resolve()
    if stop and home != Path.home().resolve():
        raise ValueError('An alternate --home requires --no-stop.')
    if stop:
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{SERVICE}'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    runtime=home/'Library/Application Support/CodexVoiceAnnouncer'
    plist=home/'Library/LaunchAgents'/(SERVICE+'.plist')
    if plist.exists():
        runtime.mkdir(parents=True,exist_ok=True)
        plist.replace(runtime/(SERVICE+'.plist.disabled'))
    app=home/'Desktop'/APP_NAME
    if app.exists():
        trash=home/'.Trash'; trash.mkdir(exist_ok=True)
        app.rename(trash/(APP_NAME+'-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
    print('Stopped. App moved to Trash; settings, history and reference audio retained.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,default=Path.home())
    p.add_argument('--no-stop',action='store_true')
    args=p.parse_args()
    try: uninstall(args.home,not args.no_stop)
    except (OSError,ValueError) as e: p.exit(1,f'Uninstall failed: {e}\n')
