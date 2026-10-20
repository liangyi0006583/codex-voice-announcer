#!/usr/bin/env python3
"""Check actual Git blobs, reporting only filenames/rules, never matched secrets."""
import argparse
import io
import json
import hashlib
from pathlib import Path
import re
import subprocess
import wave

ROOT=Path(__file__).resolve().parents[1]
PUBLIC_AUDIO={
    'voices/warden/reference.wav','voices/red-queen/reference.wav','voices/aizen/reference.wav',
    'samples/warden-zh.wav','samples/red-queen-zh.wav','samples/aizen-zh.wav',
}
FORBIDDEN_SUFFIXES={'.wav','.mp3','.m4a','.aac','.mp4','.zip','.safetensors','.pt','.bin','.sqlite','.db','.log'}
FORBIDDEN_NAMES={'settings.json','status.json','history.json','seen.json','auth.json','service.lock'}
FORBIDDEN_DIRS={'reference','models','neural-venv','backups','controls','.build','dist','.venv','__pycache__'}
RULES={
    'local-user-path':re.compile('/Users/' + r'[^/\s\x22\x27]+/'),
    'windows-user-path':re.compile(r'[A-Za-z]:\\Users\\[^\\\s]+\\'),
    'private-key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'api-token':re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}'),
    'aws-access-id':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'jwt':re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'),
    'credential-assignment':re.compile(r'(?i)(?:client_secret|refresh_token|access_token|password)\s*[=:]\s*[\x22\x27][A-Za-z0-9_/-]{24,}'),
    'remote-environment-id':re.compile(r'\benv_[0-9a-f]{20,}\b'),
    'amazon-product-id':re.compile(r'(?<![A-Za-z0-9])B0[A-Z0-9]{8}(?![A-Za-z0-9])'),
    'email-address':re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
}


def scan_blob(name,data,extra=()):
    path=Path(name); issues=[]
    if name in PUBLIC_AUDIO:
        # Only the six reviewed, explicitly published clips are allowed. Rewriting
        # PCM strips tags and local metadata; runtime/diagnostic WAVs stay forbidden.
        try:
            with wave.open(io.BytesIO(data),'rb') as audio:
                if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())!=(1,2,24000):
                    return ['unexpected-public-audio-format']
                if audio.getnframes()/audio.getframerate()>20 or len(data)>1024*1024:
                    return ['public-audio-exceeds-limit']
                if len(data)!=44+audio.getnframes()*2:
                    return ['public-audio-has-container-metadata']
            return []
        except (wave.Error,EOFError):
            return ['invalid-public-audio']
    if (path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES
        or path.name.startswith('.env') or any(p in FORBIDDEN_DIRS for p in path.parts)
        or '.app' in ''.join(path.suffixes) or path.name.endswith('progress.json')):
        issues.append('private-or-generated-file')
    if len(data)>256*1024 or b'\0' in data:
        issues.append('binary-or-oversized-file'); return issues
    try: text=data.decode('utf-8')
    except UnicodeError: return issues+['non-text-file']
    for label,pattern in RULES.items():
        if pattern.search(text): issues.append(label)
    for literal in extra:
        if literal and literal.casefold() in text.casefold(): issues.append('private-literal')
    return issues


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--staged',action='store_true')
    p.add_argument('--forbid-literal',action='append',default=[])
    args=p.parse_args()
    command=['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'] if args.staged else ['git','ls-files','-z']
    paths=subprocess.run(command,cwd=ROOT,capture_output=True,check=True).stdout.decode().split('\0')
    failures=0; count=0
    for name in filter(None,paths):
        count+=1
        entry=subprocess.run(['git','ls-files','--stage','--',name],cwd=ROOT,capture_output=True,check=True).stdout
        if entry.startswith(b'120000'):
            print(f'{name}: symlink-not-allowed'); failures+=1; continue
        data=subprocess.run(['git','show',':'+name],cwd=ROOT,capture_output=True,check=True).stdout
        issues=scan_blob(name,data,args.forbid_literal)
        for issue in issues: print(f'{name}: {issue}'); failures+=1
        if name in PUBLIC_AUDIO:
            manifest=json.loads(subprocess.run(['git','show',':voices/manifest.json'],cwd=ROOT,
                capture_output=True,check=True).stdout)
            entries={row['path']:row for row in manifest['audio_files']}
            if entries.get(name,{}).get('sha256')!=hashlib.sha256(data).hexdigest():
                print(f'{name}: public-audio-manifest-mismatch');failures+=1
    print(f'Checked {count} Git blobs; {failures} findings.')
    raise SystemExit(1 if failures else 0)


if __name__=='__main__': main()
