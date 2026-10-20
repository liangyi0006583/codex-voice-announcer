import importlib.util
import json
import hashlib
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import build
import install
import uninstall
import check_publish


class InstallTests(unittest.TestCase):
    def fake_build(self,target):
        resource=target/'Contents/Resources'; resource.mkdir(parents=True)
        for name in ['monitor.py','NeuralEngine.py','SpeechText.py','VerifySpeech.py','SpeechEngine','AudioPlayer']:
            (resource/name).write_text('synthetic test file')
        voice=resource/'voices/warden';voice.mkdir(parents=True)
        (voice/'reference.wav').write_bytes(b'synthetic fixture')
        (voice/'source.json').write_text('{}')
        return target
    def test_staged_install_uses_supplied_home_preserves_settings_and_never_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder); runtime=home/'Library/Application Support/CodexVoiceAnnouncer'
            runtime.mkdir(parents=True)
            config={'paused':True,'rate':150}; (runtime/'settings.json').write_text(json.dumps(config))
            with patch.object(install,'build_app',side_effect=self.fake_build), patch.object(install.subprocess,'run') as run:
                app=install.install(home,start=False)
            run.assert_not_called()
            self.assertTrue(app.is_dir())
            self.assertEqual(json.loads((runtime/'settings.json').read_text()),config)
            plist=home/'Library/LaunchAgents'/(build.SERVICE+'.plist')
            with plist.open('rb') as f: metadata=plistlib.load(f)
            self.assertEqual(Path(metadata['ProgramArguments'][1]),(runtime/'monitor.py').resolve())
            self.assertEqual(runtime.stat().st_mode&0o777,0o700)
    def test_uninstall_keeps_history_and_disables_service(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            with patch.object(install,'build_app',side_effect=self.fake_build): install.install(home,start=False)
            runtime=home/'Library/Application Support/CodexVoiceAnnouncer'
            (runtime/'history.json').write_text('[]')
            with patch.object(uninstall.subprocess,'run') as run: uninstall.uninstall(home,stop=False)
            run.assert_not_called()
            self.assertTrue((runtime/'history.json').exists())
            self.assertTrue((runtime/(build.SERVICE+'.plist.disabled')).exists())
            self.assertFalse((home/'Desktop'/build.APP_NAME).exists())
            self.assertEqual(len(list((home/'.Trash').iterdir())),1)
    def test_alternate_home_cannot_start_real_service(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): install.install(Path(folder),start=True)
    def test_scanner_rejects_private_paths_credentials_and_audio_without_printing_values(self):
        self.assertIn('local-user-path',check_publish.scan_blob('test.txt',('/Users/'+'example-user/private').encode()))
        self.assertIn('api-token',check_publish.scan_blob('test.txt',('sk-'+'x'*32).encode()))
        self.assertIn('private-or-generated-file',check_publish.scan_blob('reference/sample.wav',b'RIFF'))
        self.assertEqual(check_publish.scan_blob('README.md',b'Public source and synthetic tests'),[])
    def test_published_audio_matches_explicit_manifest_and_is_tag_free(self):
        root=SCRIPTS.parent
        entries=json.loads((root/'voices/manifest.json').read_text())['audio_files']
        self.assertEqual({entry['path'] for entry in entries},check_publish.PUBLIC_AUDIO)
        for entry in entries:
            data=(root/entry['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),entry['sha256'])
            self.assertEqual(check_publish.scan_blob(entry['path'],data),[])
    def test_reinstall_preserves_user_reference_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            with patch.object(install,'build_app',side_effect=self.fake_build): install.install(home,start=False)
            reference=home/'Library/Application Support/CodexVoiceAnnouncer/reference/warden/reference.wav'
            reference.write_bytes(b'user-owned local replacement')
            with patch.object(install,'build_app',side_effect=self.fake_build): install.install(home,start=False)
            self.assertEqual(reference.read_bytes(),b'user-owned local replacement')


if __name__=='__main__': unittest.main()
