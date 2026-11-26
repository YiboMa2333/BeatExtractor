"""Optional developer validation: deterministic DSP, or a quiet real output-device/GUI test."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from time import perf_counter

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def metrics(times, truth):
    remaining, errors, extra = list(truth), [], 0
    for time in times:
        if not remaining:
            extra += 1
            continue
        i = min(range(len(remaining)), key=lambda j: abs(remaining[j]-time))
        if abs(remaining[i]-time) <= .05:
            errors.append(time-remaining.pop(i))
        else:
            extra += 1
    return dict(matched=len(errors), missed=len(remaining), extra=extra,
                maxAbsoluteErrorMs=max(map(abs, errors), default=0)*1000,
                meanAbsoluteErrorMs=float(np.mean(np.abs(errors)))*1000 if errors else 0)


def validate_dsp():
    from live_dsp import StreamingFeatures
    sr = 44100
    audio = np.zeros(sr*8)
    truth = np.arange(.5, 8, .5)
    click = .5*np.sin(2*np.pi*1000*np.arange(441)/sr)*np.hanning(441)
    for time in truth:
        audio[round(time*sr):round(time*sr)+441] = click
    dsp, events, durations = StreamingFeatures(sr), [], []
    for n in range(0, len(audio), 512):
        began = perf_counter()
        row, emitted, spectrum = dsp.process(audio[n:n+512], n)
        durations.append((perf_counter()-began)*1000)
        events.extend(emitted)
    return dict(sampleRate=sr, hopSize=512, fftSize=2048, durationSeconds=8,
                groundTruthBeats=len(truth), estimatedBpm=dsp.bpm,
                onsets=metrics([e['timeSeconds'] for e in events if e['type']=='onset'], truth),
                estimatedBeats=metrics([e['timeSeconds'] for e in events if e['type']=='estimatedBeat'], truth),
                timingToleranceMs=50, note='Estimated beats intentionally omit tempo warm-up events.',
                meanProcessingMs=float(np.mean(durations)), maxProcessingMs=max(durations),
                blockBudgetMs=512/sr*1000)


def validate_hardware():
    from live_engine import LiveEngine
    from live_gui import LiveWindow
    with tempfile.TemporaryDirectory(prefix='live-hardware-') as folder:
        folder = Path(folder)
        path = folder/'quiet_hardware_smoke.wav'
        sr = 44100
        sf.write(path, .003*np.sin(2*np.pi*440*np.arange(sr*2)/sr), sr)
        engine = LiveEngine(path, folder)
        window = LiveWindow(engine)
        window.root.withdraw()  # Exercises actual Tk widgets without showing test UI.
        window.root.after(400, engine.pause)
        window.root.after(700, engine.resume)
        def finish_when_saved():
            if window.done:
                window.close()
            else:
                window.root.after(50, finish_when_saved)
        window.root.after(100, finish_when_saved)
        window.root.after(15000, window.close)
        saved, error = window.run()
        if error or saved is None:
            raise RuntimeError(error or 'Hardware session did not produce JSON.')
        data = json.loads(saved.read_text())
        if not data['completed']:
            raise RuntimeError('Hardware playback did not reach EOF.')
        result = dict(completed=data['completed'], playedDurationSeconds=data['playedDurationSeconds'],
                    features=data['recordCounts']['features'], reportedOutputLatencySeconds=data['playback']['reportedOutputLatencySeconds'],
                    diagnostics=data['diagnostics'], pauseResumeExercised=True, hiddenTkWindowExercised=True,
                    threadsStopped=not engine.decoder_thread.is_alive() and not engine.analysis_thread.is_alive(),
                    streamClosed=engine.stream.closed,
                    caveat='PortAudio API playback verified; physical speaker audibility not independently measured.')
        # A second short session explicitly verifies closing DURING playback.
        partial_engine = LiveEngine(path, folder)
        partial_window = LiveWindow(partial_engine)
        partial_window.root.withdraw()
        partial_window.root.after(600, partial_window.close)
        partial_path, partial_error = partial_window.run()
        if partial_error or partial_path is None:
            raise RuntimeError(partial_error or 'Window-close test failed to save.')
        partial = json.loads(partial_path.read_text())
        if partial['completed'] or not 0 < partial['playedDurationSeconds'] < 2:
            raise RuntimeError('Window close did not stop before EOF.')
        result['closeDuringPlayback'] = dict(completed=partial['completed'],
            playedDurationSeconds=partial['playedDurationSeconds'],
            streamClosed=partial_engine.stream.closed,
            threadsStopped=not partial_engine.decoder_thread.is_alive() and not partial_engine.analysis_thread.is_alive())
        return result


def validate_decoders():
    from live_engine import inspect_audio
    from video_export import find_tools
    results = {}
    with tempfile.TemporaryDirectory(prefix='live-decoders-') as directory:
        folder = Path(directory)
        samples = .05*np.sin(2*np.pi*440*np.arange(4410)/44100)
        for suffix in ('wav', 'flac'):
            path = folder/('decoder.'+suffix)
            sf.write(path, samples, 44100)
            results[suffix] = dict(verified=inspect_audio(path)[1].frames > 0)
        try:
            ffmpeg = find_tools()[0]  # Used only to create an MP3 TEST fixture.
            mp3 = folder/'decoder.mp3'
            subprocess.run([ffmpeg, '-v', 'error', '-i', str(folder/'decoder.wav'), str(mp3)],
                           check=True, capture_output=True)
            results['mp3'] = dict(verified=inspect_audio(mp3)[1].frames > 0)
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            results['mp3'] = dict(verified=False, reason=str(error))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', action='store_true', help='Play a very quiet 2s test tone using the default output device.')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = dict(dsp=validate_dsp(), decoders=validate_decoders())
    if args.hardware:
        report['hardware'] = validate_hardware()
    text = json.dumps(report, indent=2, allow_nan=False)
    print(text)
    if args.report:
        args.report.write_text(text+'\n', encoding='utf-8')
