import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from loopback_engine import LoopbackEngine
from test_loopback import FakeClient


@unittest.skipUnless(importlib.util.find_spec('ijson') and importlib.util.find_spec('tkinter'),
                     'Install live dependencies and Tcl/Tk for UI tests')
class ManualStartTests(unittest.TestCase):
    def factory(self,folder):
        client=FakeClient()
        api=SimpleNamespace(PyAudio=lambda:client,paWASAPI=13,paFloat32=1,
                            paContinue=0,paComplete=1,paAbort=2,paInputOverflow=2)
        return LoopbackEngine(folder,audio_api=api,export_mp4=False)

    def window(self,engine,factory=None):
        from live_gui import LiveWindow
        try:
            window=LiveWindow(engine,engine_factory=factory)
        except Exception as error:
            engine.finish()
            if error.__class__.__name__ == 'TclError':
                self.skipTest(str(error))
            raise
        window.root.withdraw()
        return window

    def test_opens_stopped_without_starting_or_creating_recordings(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            engine=self.factory(folder)
            window=self.window(engine)
            window.root.after(150,window.close)
            window.run()
            self.assertFalse(engine.started)
            self.assertIsNone(engine.audio_capture)
            self.assertIsNone(engine.recorder)
            self.assertFalse(list(folder.iterdir()))
            engine.finish()

    def test_one_button_starts_stops_and_starts_fresh_session(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            first=self.factory(folder)
            window=self.window(first,lambda:self.factory(folder))
            window.play_button.invoke()
            self.assertTrue(first.started)
            self.assertEqual(window.play_button['text'],'Stop recording')
            window.play_button.invoke()
            self.assertTrue(window.finishing)
            self.assertIn('disabled',window.play_button.state())
            def wait_then_restart():
                if not window.done or 'disabled' in window.play_button.state():
                    window.root.after(10,wait_then_restart)
                else:
                    window.play_button.invoke()
                    window.close()
            window.root.after(100,wait_then_restart)
            window.root.after(5000,window.close)
            window.run()
            self.assertIsNot(window.engine,first)
            self.assertTrue(window.engine.started)
            self.assertTrue(first.finalized)
            self.assertTrue(window.engine.finalized)
            self.assertTrue(first.terminated)
            self.assertTrue(window.engine.terminated)
            self.assertEqual(len(list(folder.glob('*_live_features*.json'))),2)


if __name__ == '__main__':
    unittest.main()
