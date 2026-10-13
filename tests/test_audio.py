"""Measure native time/pitch output and interruption/route recovery on real audio."""
import json
import os
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
import numpy as np

PLAYER = Path(os.environ.get('CODEX_VOICE_PLAYER', str(Path(__file__).resolve().parents[1] / '.build/Codex Voice Announcer.app/Contents/Resources/AudioPlayer')))


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sr = 24000
        self.input = self.root / 'input.wav'
        audio = (np.sin(2 * np.pi * 440 * np.arange(self.sr * 4) / self.sr) * 8000).astype('<i2')
        with wave.open(str(self.input), 'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(self.sr); f.writeframes(audio.tobytes())
    def tearDown(self):
        self.temp.cleanup()
    def render(self, rate=190, pitch=0, style='formal'):
        output = self.root / f'{rate}-{pitch}-{style}.wav'
        result = subprocess.run([str(PLAYER)], input=json.dumps({'file':str(self.input),'rate':rate,
            'pitchSemitones':pitch,'style':style,'output':str(output)}).encode(), capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        with wave.open(str(output), 'rb') as f:
            sr = f.getframerate(); count = f.getnframes()
            data = f.readframes(count)
            width = f.getsampwidth()
        audio = np.frombuffer(data, '<f4' if width == 4 else '<i2').astype(float)
        clip = audio[sr:2*sr]
        frequency = np.fft.rfftfreq(len(clip), 1/sr)[np.argmax(abs(np.fft.rfft(clip * np.hanning(len(clip)))))]
        self.assertGreater(np.max(abs(audio)), 0.01)
        return count/sr, frequency
    def test_speed_changes_duration_without_changing_pitch(self):
        slow, slow_pitch = self.render(150)
        fast, fast_pitch = self.render(250)
        self.assertAlmostEqual(slow, 4 * 190 / 150, delta=.04)
        self.assertAlmostEqual(fast, 4 * 190 / 250, delta=.04)
        self.assertAlmostEqual(slow_pitch, 440, delta=5)
        self.assertAlmostEqual(fast_pitch, 440, delta=5)
    def test_pitch_changes_frequency_without_changing_duration(self):
        duration, frequency = self.render(pitch=3)
        self.assertAlmostEqual(duration, 4, delta=.04)
        self.assertAlmostEqual(frequency, 440 * 2**(3/12), delta=8)
    def test_styles_make_measurably_different_audio(self):
        gentle, gentle_pitch = self.render(style='gentle')
        firm, firm_pitch = self.render(style='firm')
        self.assertGreater(gentle, firm)
        self.assertGreater(gentle_pitch, firm_pitch + 20)
    @unittest.skipUnless(os.environ.get("CODEX_VOICE_AUDIO_TESTS") == "1", "Live audio is opt-in")
    def test_route_recovery_continues_from_existing_position(self):
        progress = self.root/'progress.json'
        start = time.monotonic()
        result = subprocess.run([str(PLAYER), '--simulate-route-change'],
            input=json.dumps({'file':str(self.input),'muted':True,'progressPath':str(progress)}).encode(),
            capture_output=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        elapsed = time.monotonic()-start
        state = json.loads(progress.read_text())
        self.assertEqual(state['phase'], 'complete')
        self.assertGreaterEqual(state['routeRecoveries'], 1)
        self.assertEqual(state['frame'], self.sr * 4)
        self.assertLess(elapsed, 5.3)  # Recovery must not restart the whole recording.
        self.assertGreater(elapsed, 3.8)
    @unittest.skipUnless(os.environ.get("CODEX_VOICE_AUDIO_TESTS") == "1", "Live audio is opt-in")
    def test_settings_change_during_playback(self):
        settings = self.root/'settings.json'; progress = self.root/'progress.json'
        settings.write_text(json.dumps({'rate':190,'pitch_semitones':0,'speech_style':'formal'}))
        p = subprocess.Popen([str(PLAYER)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdin.write(json.dumps({'file':str(self.input),'muted':True,
            'settingsPath':str(settings),'progressPath':str(progress)}).encode()); p.stdin.close()
        deadline = time.monotonic()+3
        while not progress.exists() and time.monotonic()<deadline:
            time.sleep(.05)
        settings.write_text(json.dumps({'rate':250,'pitch_semitones':-2,'speech_style':'firm'}))
        p.wait(timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr.read().decode()); p.stderr.close()
        state = json.loads(progress.read_text())
        self.assertAlmostEqual(state['rateMultiplier'], 250 / 190 * 1.04, delta=.01)
        self.assertAlmostEqual(state['pitchCents'], -310, delta=1)


if __name__ == '__main__':
    unittest.main()
