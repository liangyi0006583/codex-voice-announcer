#!/usr/bin/env python3
"""Import user-owned reference audio locally; never contact a remote service."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def import_reference(data,slot,audio,text,language='auto',clone_mode='speaker_only'):
    if slot not in (1,2,3): raise ValueError('Slot must be 1, 2 or 3.')
    if not text.strip(): raise ValueError('Supply an exact reference transcript.')
    audio=Path(audio).resolve()
    if not audio.is_file(): raise ValueError('Reference audio does not exist.')
    reference=Path(data)/'reference'/f'custom-{slot}'
    reference.mkdir(parents=True,exist_ok=True); reference.chmod(0o700)
    with tempfile.TemporaryDirectory(dir=reference) as folder:
        converted=Path(folder)/'reference.wav'
        subprocess.run(['/usr/bin/afconvert','-f','WAVE','-d','LEI16@24000','-c','1',
                        str(audio),str(converted)],check=True)
        converted.replace(reference/'reference.wav')
    (reference/'reference.wav').chmod(0o600)
    metadata=reference/'source.json'
    metadata.write_text(json.dumps({'reference_text':text.strip(),'reference_language':language,
                                   'clone_mode':clone_mode},ensure_ascii=False))
    metadata.chmod(0o600)
    print(f'Reference imported into slot {slot}. Select the matching voice in the app.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--slot',type=int,choices=[1,2,3],required=True)
    p.add_argument('--language',default='auto',help='Language of the reference audio, e.g. English or Japanese')
    p.add_argument('--clone-mode',choices=['speaker_only','icl'],default='speaker_only')
    p.add_argument('--audio',type=Path,required=True)
    p.add_argument('--text',required=True,help='Exact spoken transcript of the reference')
    p.add_argument('--data-directory',type=Path,
        default=Path.home()/'Library/Application Support/CodexVoiceAnnouncer')
    args=p.parse_args(); os.umask(0o077)
    try: import_reference(args.data_directory,args.slot,args.audio,args.text,args.language,args.clone_mode)
    except (OSError,ValueError,subprocess.CalledProcessError) as e:
        p.exit(1,f'Reference import failed: {e}\n')
