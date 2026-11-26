import contextlib
import io
import importlib.util
import json
from pathlib import Path
from queue import Empty
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import cmd_app
from live_dsp import StreamingFeatures
from live_engine import LiveEngine, LiveSettings, inspect_audio, require_live_dependencies
from live_recording import LiveRecorder


def metronome(sr=44100, seconds=8):
    samples = np.zeros(sr*seconds, dtype=np.float32)
    truth = np.arange(.5, seconds, .5)
    click = .5*np.sin(2*np.pi*1000*np.arange(sr//100)/sr)*np.hanning(sr//100)
    for time in truth:
        start = round(time*sr)
        samples[start:start+len(click)] = click
    return samples, truth


def evaluate(times, truth, tolerance=.05):
    remaining = list(truth)
    errors, extras = [], 0
    for time in times:
        if not remaining:
            extras += 1
            continue
        index = min(range(len(remaining)), key=lambda i: abs(remaining[i]-time))
        if abs(remaining[index]-time) <= tolerance:
            errors.append(time-remaining.pop(index))
        else:
            extras += 1
    return dict(missed=len(remaining), extra=extras, matched=len(errors),
                maxAbsoluteErrorMs=max(map(abs, errors), default=0)*1000)


class DSPTests(unittest.TestCase):
    def run_audio(self, samples, sr=44100):
        dsp = StreamingFeatures(sr)
        rows, events = [], []
        for start in range(0, len(samples), 512):
            row, new, spectrum = dsp.process(samples[start:start+512], start)
            rows.append(row)
            events.extend(new)
        return dsp, rows, events

    def test_metronome_onsets_and_estimated_beats_against_ground_truth(self):
        samples, truth = metronome()
        dsp, rows, events = self.run_audio(samples)
        self.assertAlmostEqual(dsp.bpm, 120, delta=2)
        onsets = [e['timeSeconds'] for e in events if e['type'] == 'onset']
        beats = [e['timeSeconds'] for e in events if e['type'] == 'estimatedBeat']
        onset_metrics, beat_metrics = evaluate(onsets, truth), evaluate(beats, truth)
        self.assertLessEqual(onset_metrics['missed'], 1)
        self.assertEqual(onset_metrics['extra'], 0)
        self.assertLess(onset_metrics['maxAbsoluteErrorMs'], 50)
        self.assertEqual(beat_metrics['extra'], 0)
        self.assertGreaterEqual(beat_metrics['missed'], 5)  # Honest causal tempo warm-up.
        self.assertGreaterEqual(beat_metrics['matched'], 8)
        self.assertTrue(all(e['detectedAtSeconds'] >= e['timeSeconds'] for e in events))
        self.assertIsNone(rows[0]['estimatedBpm'])

    def test_sine_has_spectral_features_without_periodic_beats(self):
        sr = 44100
        samples = .2*np.sin(2*np.pi*440*np.arange(sr*2)/sr)
        dsp, rows, events = self.run_audio(samples)
        self.assertAlmostEqual(rows[-1]['rms'], .2/np.sqrt(2), delta=.005)
        self.assertAlmostEqual(rows[-1]['spectralCentroidHz'], 440, delta=10)
        self.assertLess(rows[-1]['spectralBandwidthHz'], 80)
        self.assertIsNone(dsp.bpm)
        self.assertFalse([e for e in events if e['type'] == 'estimatedBeat'])
        self.assertGreater(rows[-1]['midrangeEnergy'], rows[-1]['trebleEnergy'])

    def test_silence_nulls_and_exact_sample_timestamps(self):
        dsp, rows, events = self.run_audio(np.zeros(44100))
        self.assertFalse(events)
        self.assertIsNone(dsp.bpm)
        self.assertEqual(rows[0]['sampleIndex'], 512)
        self.assertEqual(rows[0]['timeSeconds'], 512/44100)
        self.assertEqual(rows[-1]['sampleIndex'], 44100)
        self.assertEqual(rows[-1]['timeSeconds'], 1)
        self.assertIsNone(rows[-1]['rmsDbfs'])
        self.assertIsNone(rows[-1]['spectralCentroidHz'])

    def test_gap_resets_rhythm_state_and_is_reported(self):
        dsp = StreamingFeatures(44100)
        dsp.process(np.zeros(512), 0)
        _, events, _ = dsp.process(np.zeros(512), 1536)
        self.assertEqual(events[0]['type'], 'analysisGap')
        self.assertEqual(events[0]['missingSamples'], 1024)
        self.assertIsNone(dsp.bpm)


class FakeStop(Exception):
    pass


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs, self.time, self.latency = kwargs, 0.0, .02
        self.closed = self.aborted = False

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root/'test with spaces.wav'
        sf.write(self.wav, np.zeros((4410, 2)), 44100)
        self.api = SimpleNamespace(OutputStream=FakeStream, CallbackStop=FakeStop)

    def engine(self, queue_blocks=2):
        return LiveEngine(self.wav, self.root, LiveSettings(queue_blocks=queue_blocks), self.api)

    def call(self, engine, dac=0.0, underflow=False):
        out = np.empty((512, 2), dtype=np.float32)
        engine.callback(out, 512, SimpleNamespace(outputBufferDacTime=dac),
                        SimpleNamespace(output_underflow=underflow))
        return out

    def test_callback_plays_identical_pcm_and_pause_resume_preserves_samples(self):
        engine = self.engine()
        engine.stream = FakeStream()
        block = np.full((512, 2), .125, dtype=np.float32)
        engine.playback_queue.put(block)
        with patch.object(engine.dsp, 'process', side_effect=AssertionError('FFT in callback')):
            np.testing.assert_array_equal(self.call(engine, 1.0), block)
        start, queued, _ = engine.analysis_queue.get_nowait()
        self.assertIs(queued, block)
        self.assertEqual(start, 0)
        engine.stream.time = 1.005
        self.assertAlmostEqual(engine.position_samples(), 220, delta=1)
        engine.pause()
        engine.playback_queue.put(block)
        self.assertTrue(np.all(self.call(engine, 1.02) == 0))
        self.assertEqual(engine.submitted_samples, 512)
        self.assertEqual(engine.playback_queue.qsize(), 1)
        engine.stream.time = 2.0
        self.assertEqual(engine.position_samples(), 512)
        engine.resume()
        self.call(engine, 2.01)
        self.assertEqual(engine.analysis_queue.get_nowait()[0], 512)
        self.assertEqual(engine.submitted_samples, 1024)

    def test_bounded_analysis_drops_and_underruns_are_counted(self):
        engine = self.engine()
        for _ in range(3):
            engine.playback_queue.put(np.zeros((512, 2), dtype=np.float32))
            self.call(engine)
        self.assertEqual(engine.stats['analysisDroppedBuffers'], 1)
        self.assertEqual(engine.stats['analysisDroppedSamples'], 512)
        self.assertEqual(engine.analysis_queue.qsize(), 2)
        self.call(engine, underflow=True)
        self.assertEqual(engine.stats['playbackUnderruns'], 1)
        self.assertEqual(engine.stats['decoderUnderruns'], 1)
        self.assertEqual(engine.submitted_samples, 1536)

    def test_future_output_buffer_does_not_advance_audible_clock(self):
        engine = self.engine()
        engine.stream = FakeStream()
        block = np.zeros((512, 2), dtype=np.float32)
        for dac in (1.0, 1.0+512/44100):
            engine.playback_queue.put(block)
            self.call(engine, dac)
        engine.stream.time = .99
        self.assertEqual(engine.position_samples(), 0)
        engine.stream.time = 1.005
        self.assertAlmostEqual(engine.position_samples(), 220, delta=1)
        engine.stream.time = 1.02
        self.assertAlmostEqual(engine.position_samples(), 882, delta=1)

    @unittest.skipUnless(importlib.util.find_spec('ijson'), 'Install requirements-live.txt for recorder tests')
    def test_decoder_worker_recorder_and_resource_cleanup(self):
        engine = self.engine(queue_blocks=8)
        engine.start()
        while True:
            # Waiting for producer scheduling is unrelated to the sample-based audio clock.
            if engine.playback_queue.empty():
                threading.Event().wait(.001)
                continue
            try:
                self.call(engine, engine.submitted_samples/44100)
            except FakeStop:
                break
            engine.analysis_queue.join()
        saved = engine.finish(completed=True)
        data = json.loads(saved.read_text())
        self.assertTrue(data['completed'])
        self.assertEqual(data['playedDurationSeconds'], .1)
        self.assertEqual(data['features'][-1]['sampleIndex'], 4410)
        self.assertTrue(engine.stream.closed)
        self.assertFalse(engine.decoder_thread.is_alive())
        self.assertFalse(engine.analysis_thread.is_alive())
        self.assertEqual(engine.finish(), saved)

    def test_missing_corrupt_and_decoder_supported_formats(self):
        with self.assertRaises(ValueError):
            inspect_audio(self.root/'missing.wav')
        corrupt = self.root/'bad.wav'
        corrupt.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            inspect_audio(corrupt)
        flac = self.root/'test.flac'
        sf.write(flac, np.zeros(4410), 44100)
        self.assertEqual(inspect_audio(flac)[1].frames, 4410)

    def test_device_start_failure_stops_workers(self):
        engine = self.engine()
        engine.audio_api = SimpleNamespace(OutputStream=lambda **_: (_ for _ in ()).throw(RuntimeError('No output device')))
        with self.assertRaisesRegex(RuntimeError, 'No output device'):
            engine.start()
        self.assertFalse(engine.decoder_thread.is_alive())
        self.assertFalse(engine.analysis_thread.is_alive())
        self.assertTrue(engine.recorder.closed)

    def test_missing_optional_dependencies_is_actionable(self):
        with patch.dict(sys.modules, {'sounddevice': None}):
            with self.assertRaisesRegex(RuntimeError, 'requirements-live.txt'):
                require_live_dependencies()


class RecorderAndMenuTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('ijson'), 'Install requirements-live.txt for recorder tests')
    def test_incremental_valid_json_stop_cutoff_and_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root/'song_live_features.json'
            existing.write_text('preserve')
            recorder = LiveRecorder(root)
            for i in range(2000):
                recorder.append('features', dict(timeSeconds=i/1000, rms=0.1, estimatedBpm=None))
            recorder.append('events', dict(type='onset', timeSeconds=.5, detectedAtSeconds=.6, strength=.2))
            saved = recorder.finish(dict(source='song.wav', mode='live', durationSeconds=2,
                                         playedDurationSeconds=1, sampleRate=44100))
            data = json.loads(saved.read_text())
            self.assertEqual(len(data['features']), 1001)
            self.assertEqual(data['recordCounts']['events'], 1)
            self.assertIsNone(data['estimatedBpm'])
            self.assertEqual(existing.read_text(), 'preserve')
            self.assertFalse(recorder.work.exists())

    def test_offline_mode_never_requests_ffmpeg_or_video(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'silence.wav'
            sf.write(path, np.zeros(4410), 44100)
            output = io.StringIO()
            with patch.object(cmd_app, 'find_tools', side_effect=AssertionError('FFmpeg in offline mode')), \
                    patch.object(cmd_app, 'export_video', side_effect=AssertionError('Video in offline mode')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cmd_app.main([str(path), '--action', 'offline', '--output-dir', folder]), 0)
            self.assertEqual(len(list(Path(folder).glob('*_features.json'))), 1)
            self.assertFalse(list(Path(folder).glob('*.mp4')))

    def test_menu_invalid_choice_and_exit(self):
        output = io.StringIO()
        with patch('builtins.input', side_effect=['invalid', '5']), contextlib.redirect_stdout(output):
            self.assertEqual(cmd_app.main(), 0)
        self.assertIn('[2] Live Analysis', output.getvalue())
        self.assertIn('Please enter 1, 2, 3, 4 or 5.', output.getvalue())


if __name__ == '__main__':
    unittest.main()
