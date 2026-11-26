import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import librosa
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import analyse
import cmd_app
from overlay import load_features


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wav(self, silent=False):
        path = self.root/'song with spaces.wav'
        samples = np.zeros(22050*8, dtype=np.float32)
        if not silent:
            for start in range(11025, len(samples), 11025):
                samples[start:start+220] = np.hanning(220) * (.3 if start < len(samples)//2 else .8)
        sf.write(path, samples, 22050)
        return path

    def test_120_bpm_features_json_and_input_preservation(self):
        path = self.wav()
        before = hashlib.sha256(path.read_bytes()).digest()
        self.assertEqual(analyse.validate_path(f' "{path}" '), path)
        stages = []
        data = analyse.analyse(path, lambda p, _: stages.append(p))
        self.assertAlmostEqual(data['estimatedBpm'], 120, delta=5)
        self.assertGreater(data['beatCount'], 10)
        self.assertEqual(data['beatCount'], len(data['beats']))
        times = [b['timeSeconds'] for b in data['beats']]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 <= t <= 8 for t in times))
        for i, beat in enumerate(data['beats'], 1):
            self.assertEqual(beat['beatIndex'], i)
            self.assertGreaterEqual(beat['amplitude'], 0)
            self.assertGreaterEqual(beat['beatStrength'], 0)
            for key in ('loudnessDb', 'timbreCentroid', 'timbreBandwidth'):
                self.assertTrue(beat[key] is None or np.isfinite(beat[key]))
        saved = analyse.save_result(data, self.root)
        self.assertTrue(saved.name.endswith('_features.json'))
        self.assertEqual(load_features(saved), data)
        second = analyse.save_result(data, self.root)
        self.assertNotEqual(saved, second)
        self.assertEqual(json.loads(saved.read_text()), data)
        self.assertEqual(stages, sorted(stages))
        self.assertNotIn(100, stages)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_targeted_frames_match_full_librosa_features(self):
        sr, hop, length = 22050, 512, 2048
        samples = (.2*np.sin(2*np.pi*440*np.arange(sr*4)/sr)).astype(np.float32)
        # More than one 128-frame batch, including left and right padded windows.
        frames = list(range(130)) + [172]
        onset = np.arange(173, dtype=float)/10
        beats = analyse.beat_features(samples, sr, frames, onset)
        rms = librosa.feature.rms(y=samples, frame_length=length, hop_length=hop)[0]
        spec = abs(librosa.stft(samples, n_fft=length, hop_length=hop))
        centroid = librosa.feature.spectral_centroid(S=spec, sr=sr)[0]
        bandwidth = librosa.feature.spectral_bandwidth(S=spec, sr=sr)[0]
        for beat, frame in zip(beats, frames):
            self.assertAlmostEqual(beat['amplitude'], float(rms[frame]), places=6)
            self.assertAlmostEqual(beat['loudnessDb'], 20*np.log10(rms[frame]), places=4)
            self.assertAlmostEqual(beat['timbreCentroid'], centroid[frame], places=4)
            self.assertAlmostEqual(beat['timbreBandwidth'], bandwidth[frame], places=4)
            self.assertEqual(beat['beatStrength'], onset[frame])
            self.assertEqual(beat['timeSeconds'], frame*hop/sr)

    def test_undefined_zero_energy_features_are_null(self):
        beats = analyse.beat_features(np.zeros(22050), 22050, [0, 20], np.zeros(44))
        for beat in beats:
            self.assertEqual(beat['amplitude'], 0)
            self.assertEqual(beat['beatStrength'], 0)
            self.assertIsNone(beat['loudnessDb'])
            self.assertIsNone(beat['timbreCentroid'])
            self.assertIsNone(beat['timbreBandwidth'])

    def test_silence_has_no_invented_beats(self):
        data = analyse.analyse(self.wav(True), lambda *_: None)
        self.assertEqual(data['estimatedBpm'], 0)
        self.assertEqual(data['beats'], [])

    def test_bad_paths_and_corrupt_wav(self):
        for name, data in [('empty.wav', b''), ('wrong.txt', b'abc')]:
            path = self.root/name
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                analyse.validate_path(str(path))
        with self.assertRaises(ValueError):
            analyse.validate_path(str(self.root/'missing.wav'))
        path = self.root/'corrupt.wav'
        path.write_bytes(b'not audio')
        with self.assertRaises(Exception):
            analyse.analyse(path, lambda *_: None)

    def test_failed_json_reopen_removes_new_file(self):
        with patch.object(analyse.json, 'load', side_effect=ValueError('Bad JSON')):
            with self.assertRaises(ValueError):
                analyse.save_result({'source': 'test.wav'}, self.root)
        self.assertFalse(list(self.root.glob('*.json')))

    def test_downloads_fallback_and_denied_output(self):
        with patch.object(analyse, '_known_downloads', side_effect=OSError('Unavailable')):
            self.assertEqual(analyse.downloads_folder(), Path.home()/'Downloads')
        with patch.object(Path, 'mkdir', side_effect=PermissionError('Access denied')):
            with self.assertRaises(PermissionError):
                analyse.save_result({'source': 'test.wav'}, self.root)

    def test_cli_failure_retry_invalid_yn_and_success(self):
        path = self.wav(True)
        output = io.StringIO()
        report = dict(width=1280, height=720, fps=24, maxErrorMs=0, meanErrorMs=0)
        with patch('builtins.input', side_effect=['missing.wav', 'maybe', 'y', str(path), 'n']), \
                patch.object(cmd_app, 'find_tools', return_value=('ffmpeg', 'ffprobe')), \
                patch.object(cmd_app, 'export_video', return_value=(self.root/'test_ecg.mp4', report)), \
                patch.object(cmd_app, 'downloads_folder', return_value=self.root), \
                contextlib.redirect_stdout(output):
            self.assertEqual(cmd_app.export_workflow(), 0)
        text = output.getvalue()
        self.assertEqual(text.count('ECG EXPORT SUCCESSFUL'), 1)
        self.assertEqual(text.count('ECG EXPORT FAILED'), 1)
        self.assertIn('Please enter Y or N.', text)
        self.assertEqual(text.count('[100% ]'), 1)

    def test_video_failure_preserves_json_without_success(self):
        path = self.wav(True)
        output = io.StringIO()
        with patch('builtins.input', side_effect=[str(path), 'N']), \
                patch.object(cmd_app, 'find_tools', return_value=('ffmpeg', 'ffprobe')), \
                patch.object(cmd_app, 'downloads_folder', return_value=self.root), \
                patch.object(cmd_app, 'export_video', side_effect=RuntimeError('Encoder failed')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(cmd_app.export_workflow(), 1)
        self.assertTrue(list(self.root.glob('*_features.json')))
        self.assertIn('Feature JSON saved to:', output.getvalue())
        self.assertNotIn('[100% ]', output.getvalue())
        self.assertNotIn('ECG EXPORT SUCCESSFUL', output.getvalue())


if __name__ == '__main__':
    unittest.main()
