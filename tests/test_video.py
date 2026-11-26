import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from analyse import save_result
from overlay import MODES, Overlay, beat_frames, load_features, quantisation_report
import video_export as video


def fixture():
    return dict(source='test song.wav', analysisMethod='librosa.offline.features.v1',
                estimatedBpm=120.0, durationSeconds=1.13, beatCount=2,
                beats=[dict(beatIndex=i+1, timeSeconds=t, amplitude=a, loudnessDb=20*np.log10(a),
                            timbreCentroid=440.0, timbreBandwidth=100.0, beatStrength=s)
                       for i, (t, a, s) in enumerate(((.25, .05, .2), (.75, .2, 1.0)))])


class RenderTests(unittest.TestCase):
    def test_intensity_changes_height_and_encoded_frame_positions(self):
        data = fixture()
        overlay = Overlay(data, (1280, 720), 24)
        self.assertLess(overlay.heights[0], overlay.heights[1])
        self.assertEqual(overlay.frames, [6, 18])
        self.assertTrue(overlay.state(18)[2])
        self.assertFalse(overlay.state(17)[2])
        x, y = overlay.marker, int(overlay.y-overlay.heights[1]*1.45)
        pixel = overlay.frame(18).getpixel((x, y))
        self.assertGreater(pixel[1], 160)
        self.assertLess(overlay.frame(15).getpixel((x, y))[1], 160)
        data['beats'], data['beatCount'] = [], 0
        self.assertFalse(Overlay(data, (1280, 720)).state(18)[2])

    def test_crossing_bar_and_peak_amplification(self):
        overlay = Overlay(fixture(), (1280, 720), 24)
        # Follow the same strong beat before crossing, at crossing, and after it.
        tips = []
        for n in (12, 18, 24):
            x = round(overlay.marker+(overlay.times[1]-n/24)*overlay.speed)
            image = overlay.frame(n)
            ys = [y for y in range(310, overlay.y-10) if image.getpixel((x,y))[1] > 180]
            tips.append(min(ys))
        self.assertLess(tips[1], tips[0]-25)
        self.assertLess(tips[1], tips[2]-25)
        at_crossing = overlay.frame(18)
        self.assertGreater(at_crossing.getpixel((overlay.marker,tips[1]))[0], 150)
        # The persistent vertical bar remains visible away from beat events.
        self.assertGreater(overlay.frame(10).getpixel((overlay.marker,325))[1], 100)

    def test_modes_quantisation_and_end_clamp(self):
        for mode, (size, fps) in MODES.items():
            data = fixture()
            data['beats'][-1]['timeSeconds'] = 1.129
            report = quantisation_report(data, fps)
            expected = [abs(n/fps-b['timeSeconds'])*1000 for n, b in zip(
                beat_frames([b['timeSeconds'] for b in data['beats']], 1.13, fps), data['beats'])]
            self.assertAlmostEqual(report['maxErrorMs'], max(expected))
            self.assertLessEqual(report['maxErrorMs'], 1000/fps)

    def test_schema_validation_and_null_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'features.json'
            data = fixture()
            for key in ('amplitude', 'loudnessDb', 'timbreCentroid', 'timbreBandwidth', 'beatStrength'):
                data['beats'][0][key] = None
            path.write_text(json.dumps(data), encoding='utf-8')
            load_features(path)
            for mutation in ('negative', 'unordered', 'count', 'nan'):
                broken = copy.deepcopy(data)
                if mutation == 'negative':
                    broken['beats'][0]['timeSeconds'] = -1
                elif mutation == 'unordered':
                    broken['beats'].reverse()
                elif mutation == 'count':
                    broken['beatCount'] = 99
                else:
                    broken['beats'][0]['beatStrength'] = float('nan')
                path.write_text(json.dumps(broken), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_features(path)

    def test_ffmpeg_progress_parser(self):
        progress = video.EncodingProgress(io.BytesIO(b'frame=24\nout_time_us=1000000\nprogress=end\n'))
        progress.join()
        self.assertEqual(progress.frames, 24)
        self.assertEqual(progress.seconds, 1)

    def test_missing_ffmpeg(self):
        with patch.object(video.shutil, 'which', return_value=None), patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'FFmpeg and FFprobe'):
                video.find_tools()


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.tools = video.find_tools()
        except RuntimeError as error:
            raise unittest.SkipTest(str(error))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture()
        self.wav = self.root/self.data['source']
        sr = 22050
        sf.write(self.wav, .2*np.sin(2*np.pi*440*np.arange(round(sr*1.13))/sr), sr)
        self.saved = save_result(self.data, self.root)

    def test_preview_streams_duration_audio_pixels_and_collisions(self):
        original = self.wav.read_bytes()
        existing = self.root/'test song_ecg.mp4'
        existing.write_bytes(b'preserve')
        stages = []
        path, report = video.export_video(self.wav, self.saved, self.root,
                                          progress=lambda p, _: stages.append(p), tools=self.tools)
        self.assertNotEqual(path, existing)
        self.assertEqual(existing.read_bytes(), b'preserve')
        info = video.validate_mp4(path, 1.13, self.tools, (1280, 720), 24)
        self.assertEqual(info['streams'][0]['avg_frame_rate'], '24/1')
        self.assertEqual(report['fps'], 24)
        self.assertEqual(stages, sorted(stages))
        self.assertNotIn(100, stages)
        self.assertEqual(self.wav.read_bytes(), original)
        self.assertFalse(list(self.root.glob('.beat-render-*')))
        decoded = subprocess.run([self.tools[0], '-v', 'error', '-i', str(path), '-vf',
                                  'select=eq(n\\,18)', '-frames:v', '1', '-f', 'rawvideo',
                                  '-pix_fmt', 'rgb24', 'pipe:1'], capture_output=True, check=True)
        image = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(720, 1280, 3)
        renderer = Overlay(self.data, (1280, 720))
        self.assertGreater(image[int(renderer.y-renderer.heights[1]*1.45), renderer.marker, 1], 150)
        audio = subprocess.run([self.tools[0], '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                '-ac', '1', '-ar', '22050', '-f', 'f32le', 'pipe:1'], capture_output=True, check=True)
        pcm = np.frombuffer(audio.stdout, dtype=np.float32)
        frequency = np.argmax(abs(np.fft.rfft(pcm)))*22050/len(pcm)
        self.assertAlmostEqual(frequency, 440, delta=3)

    def test_quality_is_1080p_30fps(self):
        path, report = video.export_video(self.wav, self.saved, self.root, mode='quality', tools=self.tools)
        video.validate_mp4(path, 1.13, self.tools, (1920, 1080), 30)
        self.assertEqual(report['fps'], 30)

    def test_failed_verification_retains_diagnostics_not_final_video(self):
        with patch.object(video, 'validate_mp4', side_effect=ValueError('Bad video')):
            with self.assertRaisesRegex(RuntimeError, 'Temporary files retained'):
                video.export_video(self.wav, self.saved, self.root, tools=self.tools)
        self.assertFalse(list(self.root.glob('*_ecg*.mp4')))
        self.assertTrue(list(self.root.glob('.beat-render-*/render.mp4')))
        self.assertTrue(self.wav.is_file())
        self.assertTrue(self.saved.is_file())

    def test_encoder_failure_preserves_input(self):
        wav_info = video.probe(self.wav, self.tools[1])
        with patch.object(video, 'probe', return_value=wav_info), \
                patch.object(video.subprocess, 'Popen', side_effect=OSError('Cannot start encoder')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot start encoder'):
                video.export_video(self.wav, self.saved, self.root, tools=self.tools)
        self.assertTrue(self.wav.is_file())
        self.assertFalse(list(self.root.glob('*_ecg*.mp4')))

    def test_disk_space_and_duration_mismatch(self):
        with patch.object(video.shutil, 'disk_usage') as disk:
            disk.return_value.free = 0
            with self.assertRaisesRegex(OSError, 'Insufficient disk'):
                video.export_video(self.wav, self.saved, self.root, tools=self.tools)
        data = fixture()
        data['durationSeconds'] = 20
        self.saved.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duration does not match'):
            video.export_video(self.wav, self.saved, self.root, tools=self.tools)


if __name__ == '__main__':
    unittest.main()
