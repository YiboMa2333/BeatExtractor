import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from test_loopback import FakeClient
from microphone_engine import MicrophoneEngine,select_microphone
from loopback_engine import select_loopback
import cmd_app


class FakeMicClient(FakeClient):
    def get_default_wasapi_device(self,*,d_in=False,d_out=False):
        assert d_in and not d_out
        return self.get_device_info_by_index(9)

    def get_device_info_by_index(self,index):
        if index==9:
            return dict(index=9,name='Test microphone',hostApi=2,defaultSampleRate=48000,
                        maxInputChannels=2,isLoopbackDevice=False)
        return super().get_device_info_by_index(index)


class MicrophoneTests(unittest.TestCase):
    def api(self,client):
        return SimpleNamespace(PyAudio=lambda:client,paWASAPI=13,paFloat32=1,
                               paContinue=0,paComplete=1,paAbort=2,paInputOverflow=2)

    def test_microphone_and_speaker_inputs_are_separate(self):
        client=FakeMicClient()
        api=self.api(client)
        self.assertEqual(select_microphone(client,api)['index'],9)
        with self.assertRaisesRegex(ValueError,'not a speaker-loopback'):
            select_microphone(client,api,10)
        with self.assertRaisesRegex(ValueError,'Microphone devices are not accepted'):
            select_loopback(client,api,9)

    def test_gain_is_shared_by_recording_and_analysis_and_clips_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=MicrophoneEngine(Path(folder),audio_api=self.api(FakeMicClient()),export_mp4=False)
            try:
                quiet=np.full((512,2),.001,dtype=np.float32)
                np.testing.assert_array_equal(engine.transform_capture_block(quiet),quiet)
                engine.set_gain_db(20)
                boosted=engine.transform_capture_block(quiet)
                np.testing.assert_allclose(boosted,.01,rtol=1e-6)
                engine.capture_callback(quiet.tobytes(),512,{},0)
                _,analysed,_=engine.analysis_queue.get_nowait()
                np.testing.assert_allclose(analysed,boosted)
                self.assertEqual(engine.capture_metadata()['finalInputGainDb'],20)
                self.assertTrue(np.all(engine.transform_capture_block(np.ones((512,2),dtype=np.float32))==1))
                with self.assertRaises(ValueError):
                    engine.set_gain_db(float('nan'))
            finally:
                engine.finish()

    def test_mic_capture_metadata_recording_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            client=FakeMicClient()
            engine=MicrophoneEngine(Path(folder),audio_api=self.api(client),export_mp4=False)
            self.assertFalse(engine.started)
            self.assertFalse(engine.is_loopback)
            self.assertTrue(engine.is_microphone)
            engine.start()
            self.assertEqual(client.opened['input_device_index'],9)
            self.assertTrue(client.opened['input'])
            for _ in range(4):
                engine.capture_callback(np.zeros((512,2),dtype=np.float32).tobytes(),512,{},0)
                engine.analysis_queue.join()
            saved=engine.finish()
            data=json.loads(saved.read_text())
            self.assertTrue(data['capture']['microphone'])
            self.assertEqual(data['sourceType'],'windows.wasapi.microphone')
            self.assertTrue(saved.name.startswith('microphone_live_features'))
            self.assertFalse(data['capture']['outputPlayback'])
            self.assertTrue(client.terminated)
            self.assertFalse(engine.audio_capture.thread.is_alive())

    def test_menu_microphone_choice_has_no_file_prompt(self):
        output=io.StringIO()
        with patch('builtins.input',side_effect=['4','5']) as user_input, \
                patch('live_gui.run_microphone',return_value=(None,None)) as microphone, \
                contextlib.redirect_stdout(output):
            self.assertEqual(cmd_app.main(),0)
        self.assertEqual(user_input.call_count,2)
        self.assertTrue(microphone.called)
        self.assertIn('[4] Microphone Analysis',output.getvalue())
        self.assertNotIn('file path:',output.getvalue())


if __name__ == '__main__':
    unittest.main()
