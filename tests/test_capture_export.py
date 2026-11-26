import json
from pathlib import Path
from queue import Full
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from capture_audio import CaptureAudio
from live_recording import LiveRecorder
from live_export import LiveCaptureOverlay, load_live_summary
from video_export import export_video, find_tools, validate_mp4
from test_loopback import FakeClient
from loopback_engine import LoopbackEngine
from live_engine import LiveSettings
from types import SimpleNamespace


class AudioRecordingTests(unittest.TestCase):
    def test_exact_pcm_and_gap_silence_preserve_sample_timeline(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = CaptureAudio(Path(folder),48000,2)
            first = np.full((512,2),.125,dtype=np.float32)
            last = np.full((512,2),-.25,dtype=np.float32)
            capture.offer(0,first)
            capture.offer(1024,last)
            path = capture.finish(1536)
            audio,sr = sf.read(path,dtype='float32',always_2d=True)
            self.assertEqual(sr,48000)
            self.assertEqual(len(audio),1536)
            np.testing.assert_array_equal(audio[:512],first)
            np.testing.assert_array_equal(audio[512:1024],np.zeros((512,2)))
            np.testing.assert_array_equal(audio[1024:],last)
            self.assertEqual(capture.stats['silenceReplacementSamples'],512)
            self.assertFalse(capture.thread.is_alive())
            capture.cleanup()
            self.assertFalse(capture.work.exists())

    def test_audio_queue_drop_is_counted_and_tail_is_padded(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = CaptureAudio(Path(folder),48000,1)
            with patch.object(capture.queue,'put_nowait',side_effect=Full):
                capture.offer(0,np.ones((512,1),dtype=np.float32))
            audio,_ = sf.read(capture.finish(512),dtype='float32')
            self.assertTrue(np.all(audio == 0))
            self.assertEqual(capture.stats['droppedBuffers'],1)
            self.assertEqual(capture.stats['silenceReplacementSamples'],512)

    def test_pause_keeps_recording_audio_and_json_precedes_video_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FakeClient()
            api = SimpleNamespace(PyAudio=lambda:client,paWASAPI=13,paFloat32=1,
                paContinue=0,paComplete=1,paAbort=2,paInputOverflow=2)
            engine = LoopbackEngine(Path(folder),LiveSettings(queue_blocks=8),api)
            engine.start()
            for i in range(8):
                if i == 2:
                    engine.pause()
                if i == 4:
                    engine.resume()
                block = np.full((512,2),.125,dtype=np.float32)
                engine.capture_callback(block.tobytes(),512,{},0)
                engine.analysis_queue.join()
            def encoder_fails(wav,json_path,*args,**kwargs):
                self.assertTrue(json_path.is_file())
                data = json.loads(json_path.read_text())
                self.assertEqual(data['durationSeconds'],4096/48000)
                audio,_ = sf.read(wav,dtype='float32')
                self.assertEqual(len(audio),4096)
                self.assertTrue(np.all(audio == .125))
                raise RuntimeError('Encoder unavailable')
            with patch('video_export.export_video',side_effect=encoder_fails):
                saved = engine.finish()
            self.assertTrue(saved.is_file())
            self.assertIn('JSON saved; MP4 export failed',engine.error)
            self.assertTrue(engine.audio_capture.path.is_file())
            self.assertFalse(engine.audio_capture.thread.is_alive())


class LiveVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.tools = find_tools()
            import ijson
        except (RuntimeError,ImportError) as error:
            raise unittest.SkipTest(str(error))

    def test_live_events_drive_encoded_video_with_captured_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            sr,duration = 48000,1.0
            wav = folder/'captured.wav'
            audio = .1*np.sin(2*np.pi*440*np.arange(sr)/sr)
            sf.write(wav,audio,sr,subtype='FLOAT',format='RF64')
            recorder = LiveRecorder(folder)
            recorder.append('features',dict(timeSeconds=.1,estimatedBpm=None,rms=.1))
            recorder.append('features',dict(timeSeconds=.6,estimatedBpm=120,rms=.1))
            recorder.append('events',dict(type='onset',timeSeconds=.25,detectedAtSeconds=.28,strength=.6))
            recorder.append('events',dict(type='estimatedBeat',timeSeconds=.75,detectedAtSeconds=.78,strength=.6))
            saved = recorder.finish(dict(source='system_audio',sourceType='windows.wasapi.loopback',
                sampleRate=sr,durationSeconds=duration,analysis=dict(fftSize=2048,hopSize=512)))
            summary = load_live_summary(saved)
            renderer = LiveCaptureOverlay(saved,(1280,720),24,summary)
            try:
                frame = renderer.frame(18)
                # Strong estimated beat grows at its exact mapped crossing frame.
                self.assertGreater(frame.getpixel((768,330))[1],150)
            finally:
                renderer.close()
            path,report = export_video(wav,saved,folder,tools=self.tools,source_format='live')
            self.assertIn('system_audio_live_ecg',path.name)
            validate_mp4(path,duration,self.tools,(1280,720),24)
            self.assertEqual(report['eventCount'],2)
            self.assertEqual(report['maxErrorMs'],0)
            # The captured audio source is retained by the encoder; only the engine cleans its own temp source.
            self.assertTrue(wav.is_file())
            import subprocess
            run = subprocess.run([self.tools[0],'-v','error','-i',str(path),'-map','0:a:0',
                '-ac','1','-ar','48000','-f','f32le','pipe:1'],capture_output=True,check=True)
            pcm = np.frombuffer(run.stdout,dtype=np.float32)
            peak = np.argmax(abs(np.fft.rfft(pcm)))*48000/len(pcm)
            self.assertAlmostEqual(peak,440,delta=3)


if __name__ == '__main__':
    unittest.main()
