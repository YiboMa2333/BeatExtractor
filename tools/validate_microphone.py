"""Opt-in microphone capture/GUI/JSON+MP4 smoke test; temporary recording is deleted."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def validate():
    from microphone_engine import MicrophoneEngine
    from live_gui import LiveWindow
    from video_export import find_tools,probe
    with tempfile.TemporaryDirectory(prefix='microphone-validation-') as folder:
        engine=MicrophoneEngine(Path(folder))
        window=LiveWindow(engine)
        window.root.withdraw()
        if engine.started:
            raise AssertionError('Microphone started without a Start click.')
        window.root.after(100,window.play_button.invoke)
        window.root.after(1500,window.close)
        saved,error=window.run()
        if error or not saved or not engine.video_path:
            raise RuntimeError(error or 'Microphone did not produce JSON and MP4.')
        data=json.loads(saved.read_text())
        if not data['features'] or not data['capture']['microphone']:
            raise RuntimeError('No microphone PCM measurements received.')
        info=probe(engine.video_path,find_tools()[1])
        return dict(sourceType=data['sourceType'],deviceName=data['capture']['deviceName'],
            sampleRate=data['sampleRate'],durationSeconds=data['durationSeconds'],
            featureRows=data['recordCounts']['features'],microphone=data['capture']['microphone'],
            audioPlayback=data['capture']['outputPlayback'],
            videoCodec=next(s['codec_name'] for s in info['streams'] if s['codec_type']=='video'),
            audioCodec=next(s['codec_name'] for s in info['streams'] if s['codec_type']=='audio'),
            startedByButton=True,streamReleased=engine.stream_released,backendTerminated=engine.terminated,
            workerStopped=not engine.analysis_thread.is_alive(),diagnostics=data['diagnostics'],
            temporaryAudioRemoved=not engine.audio_capture.path.exists())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware',action='store_true',required=True,help='Capture the real microphone briefly.')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    result=validate()
    text=json.dumps(result,indent=2,ensure_ascii=True,allow_nan=False)
    print(text)
    if args.report:
        args.report.write_text(text+'\n',encoding='utf-8')
