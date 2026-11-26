import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import analyse
import cmd_app


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wav(self, silent=False):
        path = self.root / 'song with spaces.wav'
        audio = np.zeros(22050 * 16)
        if not silent:
            for start in range(11025, len(audio), 11025):
                audio[start:start + 220] = np.hanning(220)
        sf.write(path, audio, 22050)
        return path

    def test_known_bpm_spaces_progress_and_collision(self):
        path = self.wav()
        before = hashlib.sha256(path.read_bytes()).digest()
        self.assertEqual(analyse.validate_path(f' "{path}" '), path)
        stages = []
        result = analyse.analyse(path, lambda p, _: stages.append(p))
        self.assertAlmostEqual(result['estimatedBpm'], 120, delta=5)
        self.assertGreater(result['beatCount'], 20)
        self.assertEqual(stages, sorted(stages))
        self.assertNotIn(100, stages)
        times = result['beatTimesSeconds']
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 <= t <= result['durationSeconds'] for t in times))
        folder = self.root / 'new Downloads'
        first = analyse.save_result(result, folder)
        second = analyse.save_result(result, folder)
        self.assertNotEqual(first, second)
        self.assertEqual(json.loads(second.read_text()), result)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_silence(self):
        result = analyse.analyse(self.wav(True), lambda *_: None)
        self.assertEqual(result['estimatedBpm'], 0)
        self.assertEqual(result['beatTimesSeconds'], [])

    def test_bad_files(self):
        for name, data in [('empty.wav', b''), ('wrong.txt', b'text')]:
            path = self.root / name
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                analyse.validate_path(str(path))
        with self.assertRaises(ValueError):
            analyse.validate_path(str(self.root / 'missing.wav'))
        path = self.root / 'invalid.wav'
        path.write_bytes(b'not audio')
        with self.assertRaises(Exception):
            analyse.analyse(path, lambda *_: None)

    def test_unavailable_output(self):
        result = {'source': 'song.wav'}
        path = self.root / 'blocked'
        path.write_text('file in place of folder')
        with self.assertRaises(OSError):
            analyse.save_result(result, path)
        with patch.object(Path, 'mkdir', side_effect=PermissionError('Access denied')):
            with self.assertRaises(PermissionError):
                analyse.save_result(result, self.root / 'Downloads')

    def test_downloads_resolution(self):
        if sys.platform == 'win32':
            self.assertTrue(analyse.downloads_folder().is_absolute())
        with patch.object(analyse, '_known_downloads', side_effect=OSError('API unavailable')):
            self.assertEqual(analyse.downloads_folder(), Path.home() / 'Downloads')

    def test_failed_validation_removes_output(self):
        with patch.object(analyse.json, 'load', side_effect=ValueError('Invalid JSON')):
            with self.assertRaises(ValueError):
                analyse.save_result({'source': 'song.wav'}, self.root)
        self.assertFalse(list(self.root.glob('*.json')))

    def test_interaction_failure_retry_and_success(self):
        path = self.wav(True)
        output = io.StringIO()
        with patch('builtins.input', side_effect=['missing.wav', 'invalid', 'y', str(path), 'n']), \
                patch.object(cmd_app, 'downloads_folder', return_value=self.root), \
                contextlib.redirect_stdout(output):
            cmd_app.main()
        text = output.getvalue()
        self.assertEqual(text.count('ANALYSIS FAILED'), 1)
        self.assertEqual(text.count('ANALYSIS SUCCESSFUL!'), 1)
        self.assertIn('Please enter Y or N.', text)
        self.assertEqual(text.count('[100% ]'), 1)

    def test_save_failure_never_reports_success(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=[str(self.wav(True)), 'N']), \
                patch.object(cmd_app, 'save_result', side_effect=PermissionError('Access denied')), \
                contextlib.redirect_stdout(output):
            cmd_app.main()
        self.assertNotIn('[100% ]', output.getvalue())
        self.assertNotIn('ANALYSIS SUCCESSFUL', output.getvalue())


if __name__ == '__main__':
    unittest.main()
