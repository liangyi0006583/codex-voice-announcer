import importlib.util
import json
import os
import wave
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np

RESOURCES = Path(__file__).resolve().parents[1] / 'src'
PLAYER = Path(os.environ.get('CODEX_VOICE_PLAYER', str(RESOURCES.parent / '.build/Codex Voice Announcer.app/Contents/Resources/AudioPlayer')))
spec = importlib.util.spec_from_file_location('neural', RESOURCES/'NeuralEngine.py')
neural = importlib.util.module_from_spec(spec); spec.loader.exec_module(neural)


class NeuralTests(unittest.TestCase):
    def test_pause_effects_preserve_voiced_content_and_short_gaps(self):
        sr = 24000
        voiced = np.sin(np.arange(sr)*.15).astype(np.float32)*.3
        audio = np.r_[voiced, np.zeros(sr//2), voiced]
        gentle = neural.shape_pauses(audio, sr, 'gentle')
        firm = neural.shape_pauses(audio, sr, 'firm')
        self.assertGreater(len(gentle), len(audio))
        self.assertLess(len(firm), len(audio))
        for result in [gentle, firm]:
            np.testing.assert_array_equal(result[:sr], voiced)
            np.testing.assert_array_equal(result[-sr:], voiced)
        short = np.r_[voiced,np.zeros(sr//10),voiced]
        np.testing.assert_array_equal(neural.shape_pauses(short,sr,'firm'),short)
    @unittest.skipUnless(os.environ.get("CODEX_VOICE_AUDIO_TESTS") == "1", "Live audio is opt-in")
    def test_stopping_parent_also_stops_native_playback(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = Path(folder)/'progress.json'
            sample = Path(folder)/'sample.wav'
            with wave.open(str(sample), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
                f.writeframes((np.sin(np.arange(24000*4)*.1)*8000).astype('<i2').tobytes())
            child = subprocess.Popen([str(PLAYER)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            child.stdin.write(json.dumps({'file':str(sample),'muted':True,'progressPath':str(progress)}).encode())
            child.stdin.close()
            neural.playback_process = child
            try:
                deadline = time.monotonic()+3
                while not progress.exists() and time.monotonic()<deadline:
                    time.sleep(.05)
                self.assertIsNone(child.poll())
                self.assertTrue(progress.exists())
                start = time.monotonic()
                with self.assertRaises(SystemExit):
                    neural.stop(signal.SIGTERM, None)
                self.assertIsNotNone(child.poll())
                self.assertLess(time.monotonic()-start,2)
            finally:
                if child.poll() is None: child.kill(); child.wait()
                child.stderr.close(); neural.playback_process=None


if __name__ == '__main__':
    unittest.main()
