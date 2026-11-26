import contextlib
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import cmd_app
from live_engine import LiveSettings
from loopback_engine import LoopbackEngine, require_loopback_dependencies


class FakeCapture:
    def __init__(self):
        self.active, self.closed = False, False

    def get_input_latency(self):
        return .01

    def start_stream(self):
        self.active = True

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.terminated = False
        self.stream = FakeCapture()
        self.opened = None

    def get_default_wasapi_loopback(self):
        return dict(index=10, name='Test speakers [Loopback]', hostApi=2,
                    defaultSampleRate=48000, maxInputChannels=2, isLoopbackDevice=True)

    def get_device_info_by_index(self, index):
        return dict(self.get_default_wasapi_loopback(), isLoopbackDevice=index == 10)

    def get_host_api_info_by_index(self, index):
        return dict(type=13)

    def open(self, **kwargs):
        self.opened = kwargs
        return self.stream

    def terminate(self):
        self.terminated = True


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.client = FakeClient()
        self.api = SimpleNamespace(PyAudio=lambda: self.client, paWASAPI=13, paFloat32=1,
                                   paContinue=0, paComplete=1, paAbort=2, paInputOverflow=2)

    def engine(self, blocks=2):
        return LoopbackEngine(self.folder, LiveSettings(queue_blocks=blocks), self.api, export_mp4=False)

    def pcm(self):
        return np.full((512, 2), .125, dtype=np.float32).tobytes()

    def test_default_device_is_loopback_and_mic_is_rejected(self):
        engine = self.engine()
        self.assertEqual(engine.device_info['index'], 10)
        engine.finish()
        self.assertTrue(self.client.terminated)
        with self.assertRaisesRegex(ValueError, 'Microphone devices are not accepted'):
            LoopbackEngine(self.folder, LiveSettings(device=1), self.api)

    def test_callback_samples_pause_and_queue_drops(self):
        engine = self.engine()
        raw = self.pcm()
        self.assertEqual(engine.capture_callback(raw, 512, {}, 0), (None, 0))
        start, block, _ = engine.analysis_queue.get_nowait()
        self.assertEqual(start, 0)
        self.assertEqual(block.tobytes(), raw)
        engine.pause()
        engine.capture_callback(raw, 512, {}, 0)
        self.assertEqual(engine.position_samples(), 1024)
        self.assertEqual(engine.analysis_queue.qsize(), 0)
        engine.resume()
        for _ in range(3):
            engine.capture_callback(raw, 512, {}, 0)
        self.assertEqual(engine.stats['analysisDroppedBuffers'], 1)
        self.assertEqual(engine.stats['analysisPausedSamples'], 512)
        self.assertEqual(engine.analysis_queue.get_nowait()[0], 1024)
        engine.finish()

    def test_bad_block_and_capture_overflow_are_reported(self):
        engine = self.engine()
        engine.capture_callback(self.pcm(), 512, {}, 2)
        self.assertEqual(engine.stats['captureOverflows'], 1)
        self.assertEqual(engine.capture_callback(b'bad', 512, {}, 0), (None, 2))
        self.assertIn('block size', engine.error)
        engine.finish()

    @unittest.skipUnless(importlib.util.find_spec('ijson'), 'Install requirements-live.txt')
    def test_capture_start_final_json_and_cleanup(self):
        engine = self.engine(blocks=8)
        engine.start()
        self.assertTrue(self.client.opened['input'])
        self.assertEqual(self.client.opened['input_device_index'], 10)
        self.assertNotIn('output', self.client.opened)
        for _ in range(4):
            engine.capture_callback(self.pcm(), 512, {}, 0)
            engine.analysis_queue.join()
        saved = engine.finish()
        data = json.loads(saved.read_text())
        self.assertEqual(data['sourceType'], 'windows.wasapi.loopback')
        self.assertFalse(data['capture']['microphone'])
        self.assertFalse(data['capture']['outputPlayback'])
        self.assertEqual(data['features'][-1]['sampleIndex'], 2048)
        self.assertEqual(data['durationSeconds'], 2048/48000)
        self.assertTrue(saved.name.startswith('system_audio_live_features'))
        self.assertTrue(self.client.stream.closed)
        self.assertTrue(self.client.terminated)
        self.assertFalse(engine.analysis_thread.is_alive())

    def test_missing_loopback_dependency_is_actionable(self):
        with patch.dict(sys.modules, {'pyaudiowpatch': None}):
            with self.assertRaises(RuntimeError):
                require_loopback_dependencies()

    def test_live_menu_does_not_request_any_file(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['2', '5']) as user_input, \
                patch('live_gui.run_loopback', return_value=(self.folder/'system_audio_live_features.json', None)) as capture, \
                contextlib.redirect_stdout(output):
            self.assertEqual(cmd_app.main(), 0)
        self.assertEqual(user_input.call_count, 2)
        self.assertTrue(capture.called)
        self.assertNotIn('file path:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
