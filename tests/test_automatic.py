"""End-to-end regression through the real Windows launcher, with CPU rendering."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from overlay import load_features
from video_export import find_tools, validate_mp4


class LauncherTests(unittest.TestCase):
    def test_real_bat_preview_defaults_and_no_gpu_imports(self):
        if sys.platform != 'win32':
            self.skipTest('Windows BAT launcher')
        try:
            tools = find_tools()
        except RuntimeError as error:
            self.skipTest(str(error))
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='ecg-launcher-') as directory:
            wav = Path(directory)/'synthetic ECG 120 BPM.wav'
            sr = 22050
            samples = np.zeros(sr*8)
            for start in range(sr//2, len(samples), sr//2):
                samples[start:start+220] = np.hanning(220)*(.4 if start < sr*4 else .8)
            sf.write(wav, samples, sr)
            run = subprocess.run(['cmd.exe', '/d', '/c', str(project/'run_beat_tracker.bat'), '--no-pause'],
                                 input=f'3\n"{wav}"\nN\n5\n', capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', cwd=directory, timeout=180)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            self.assertIn('ECG EXPORT SUCCESSFUL', run.stdout)
            self.assertIn('1280x720, 24 FPS', run.stdout)
            self.assertIn('Would you like to analyse another file?', run.stdout)
            self.assertNotIn('OpenGL', run.stdout)
            self.assertNotIn('procedural fallback', run.stdout)
            self.assertEqual(run.stdout.count('[100% ]'), 1)
            self.assertNotIn('100.0%', run.stdout)
            saved = Path(run.stdout.split('\nJSON:\n')[1].splitlines()[0])
            video = Path(run.stdout.split('\nVIDEO:\n')[1].splitlines()[0])
            try:
                self.assertIn('_features', saved.name)
                self.assertIn('_ecg', video.name)
                data = load_features(saved)
                self.assertAlmostEqual(data['estimatedBpm'], 120, delta=5)
                self.assertGreater(data['beatCount'], 10)
                validate_mp4(video, 8, tools, (1280, 720), 24)
            finally:
                saved.unlink(missing_ok=True)
                video.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
