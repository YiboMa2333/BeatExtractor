"""Opt-in real WASAPI/hidden-GUI check using a quiet test tone on Windows output."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def validate():
    import sounddevice as sd  # Test stimulus only; normal loopback mode never plays audio.
    from live_gui import LiveWindow
    from loopback_engine import LoopbackEngine
    with tempfile.TemporaryDirectory(prefix='wasapi-loopback-') as folder:
        engine = LoopbackEngine(Path(folder))
        window = LiveWindow(engine)
        window.root.withdraw()
        window.root.after(100,window.play_button.invoke)  # Simulate an explicit user Start click.
        sr = engine.info.samplerate
        tone = .005*np.sin(2*np.pi*440*np.arange(sr*2)/sr)
        def start_tone():
            sd.play(tone, samplerate=sr)
        window.root.after(200, start_tone)
        window.root.after(700, engine.pause)
        window.root.after(950, engine.resume)
        window.root.after(2400, window.close)
        try:
            saved, error = window.run()
        finally:
            sd.stop()
        if error or not saved:
            raise RuntimeError(error or 'No loopback recording saved.')
        data = json.loads(saved.read_text())
        max_peak = max((row['peakAmplitude'] for row in data['features']), default=0)
        if max_peak < .001 or not data['features']:
            raise RuntimeError('No test audio measured from loopback; check Windows output routing/volume.')
        from video_export import probe, find_tools
        if not engine.video_path or not engine.video_path.is_file():
            raise RuntimeError('Captured JSON did not produce an ECG MP4.')
        video = probe(engine.video_path, find_tools()[1])
        v = next(s for s in video['streams'] if s['codec_type']=='video')
        a = next(s for s in video['streams'] if s['codec_type']=='audio')
        return dict(sourceType=data['sourceType'], deviceName=data['capture']['deviceName'],
                    sampleRate=data['sampleRate'], captureDurationSeconds=data['durationSeconds'],
                    featureRows=data['recordCounts']['features'], measuredPeakAmplitude=max_peak,
                    reportedInputLatencySeconds=data['capture']['reportedInputLatencySeconds'],
                    microphone=data['capture']['microphone'], captureReplaysAudio=data['capture']['outputPlayback'],
                    diagnostics=data['diagnostics'], streamReleased=engine.stream_released,
                    backendTerminated=engine.terminated, workerStopped=not engine.analysis_thread.is_alive(),
                    videoExportVerified=True, videoCodec=v['codec_name'], audioCodec=a['codec_name'],
                    videoFrames=int(v['nb_frames']), videoFrameRate=v['avg_frame_rate'],
                    temporaryAudioRemoved=not engine.audio_capture.path.exists(),
                    hiddenGuiAndPauseResumeVerified=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true', required=True,
                        help='Explicitly capture system output and play a quiet 2s test tone.')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = validate()
    text = json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False)
    print(text)
    if args.report:
        args.report.write_text(text+'\n', encoding='utf-8')
