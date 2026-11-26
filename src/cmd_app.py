"""Simple repeatable CMD song-feature extraction and ECG export."""
import argparse
from pathlib import Path
import sys
from time import perf_counter

try:
    from analyse import analyse, downloads_folder, save_result, validate_path
    from video_export import export_video, find_tools
except ImportError as error:
    print(f'[ERROR] Missing dependency: {error}', flush=True)
    print('Install with .venv\\Scripts\\python.exe -m pip install -r requirements.txt', flush=True)
    raise SystemExit(1)


def progress(percent, message):
    print(f'[{percent:3d}% ] {message}', flush=True)


def another():
    while True:
        answer = input('\nWould you like to analyse another file? (Y/N): ').strip().upper()
        if answer in ('Y', 'N'):
            return answer == 'Y'
        print('Please enter Y or N.', flush=True)


def export_workflow(argv=None):
    parser = argparse.ArgumentParser(description='WAV features and a lightweight ECG MP4.')
    parser.add_argument('wav', nargs='?', help='Optional path for a single noninteractive run.')
    parser.add_argument('--mode', choices=('preview', 'quality'), default='preview',
                        help='preview: 720p/24 FPS; quality: 1080p/30 FPS')
    parser.add_argument('--output-dir', type=Path, help='Optional test/batch destination; default: Downloads.')
    args = parser.parse_args(argv or [])
    print('='*52 + '\n   Super Happy Space 2.0\n   SONG FEATURES & ECG EXPORTER\n' + '='*52, flush=True)
    try:
        while True:
            saved, code = None, 0
            try:
                path = validate_path(args.wav if args.wav else input('\nEnter WAV file path:\n> '))
                tools = find_tools()
                started = perf_counter()  # Measurement only; excludes typing time, never controls frames.
                print('Processing audio... this stage may take some time.', flush=True)
                result = analyse(path, lambda p, m: progress(int(p*.6), m))
                folder = args.output_dir if args.output_dir is not None else downloads_folder()
                saved = save_result(result, folder)
                progress(60, 'Feature JSON saved and reopened successfully.')
                analysed = perf_counter()
                if not result['beatCount']:
                    print('No reliable beats detected: BPM 0 and an empty beat array. The ECG remains flat.', flush=True)
                video, report = export_video(path, saved, folder, mode=args.mode, progress=progress, tools=tools)
                progress(100, 'Both outputs completed and validated!')
                elapsed = perf_counter()-started
                print('\n' + '='*52 + '\nFEATURE EXTRACTION AND ECG EXPORT SUCCESSFUL\n' + '='*52)
                print(f"Input: {path.name}\nEstimated BPM: {result['estimatedBpm']:.1f}\n"
                      f"Detected beats: {result['beatCount']}\nDuration: {result['durationSeconds']:.3f}s\n"
                      f"Mode: {args.mode} ({report['width']}x{report['height']}, {report['fps']} FPS)\n"
                      f'\nJSON:\n{saved}\n\nVIDEO:\n{video}')
                print(f"\nBeat-to-frame error: max {report['maxErrorMs']:.3f} ms, mean {report['meanErrorMs']:.3f} ms.\n"
                      f'Analysis/JSON: {analysed-started:.2f}s; render/verification: {perf_counter()-analysed:.2f}s; total: {elapsed:.2f}s.\n'
                      'Offline musical visualisation, not a medical ECG or a real-time latency result.', flush=True)
            except Exception as error:
                code = 1
                print('\nFEATURE EXTRACTION / ECG EXPORT FAILED', flush=True)
                print(f'Reason: {error}', flush=True)
                print(f'Feature JSON saved to: {saved}\nNo completed MP4 was saved.' if saved
                      else 'No output files were saved. Please check the path and try again.', flush=True)
            if args.wav or not another():
                return code
    except (EOFError, KeyboardInterrupt):
        print('\nExporter closed. Any completed feature JSON is preserved.', flush=True)
        return 130


def offline_workflow(raw, folder):
    from overlay import load_features
    try:
        path = validate_path(raw if raw else input('\nEnter WAV file path:\n> '))
        result = analyse(path, progress)
        saved = save_result(result, folder)
        load_features(saved)
        progress(100, 'Offline features saved and validated.')
        print(f'OFFLINE ANALYSIS SUCCESSFUL\nJSON:\n{saved}', flush=True)
        return 0
    except Exception as error:
        print(f'OFFLINE ANALYSIS FAILED\nReason: {error}', flush=True)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline features, live analysis, or ECG video export.')
    parser.add_argument('wav', nargs='?')
    parser.add_argument('--action', choices=('offline', 'live', 'video'))
    parser.add_argument('--mode', choices=('preview', 'quality'), default='preview')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--block-size', type=int, default=512)
    parser.add_argument('--fft-size', type=int, default=2048)
    parser.add_argument('--gui-fps', type=int, default=30)
    parser.add_argument('--queue-blocks', type=int, default=32)
    parser.add_argument('--device', type=int)
    parser.add_argument('--live-source', choices=('loopback', 'file'), default='loopback')
    parser.add_argument('--list-loopback-devices', action='store_true')
    args = parser.parse_args(argv or [])

    def dispatch(action):
        if action == 'video':
            video_args = ['--mode', args.mode]
            if args.wav:
                video_args.insert(0, args.wav)
            if args.output_dir:
                video_args += ['--output-dir', str(args.output_dir)]
            return export_workflow(video_args)
        folder = args.output_dir if args.output_dir is not None else downloads_folder()
        if action == 'offline':
            return offline_workflow(args.wav, folder)
        try:
            from live_engine import LiveSettings
            settings = LiveSettings(args.block_size, args.fft_size, args.gui_fps, args.queue_blocks, args.device)
            if args.live_source == 'loopback':
                if args.wav:
                    raise ValueError('System-audio mode does not take a file. Use --live-source file for local playback.')
                from live_gui import run_loopback
                print('Capturing the Windows default output device. Play audio in any app. No microphone is used.', flush=True)
                saved, error = run_loopback(folder, settings)
            else:
                from live_engine import require_live_dependencies
                require_live_dependencies()
                from live_gui import run_live
                raw = args.wav if args.wav else input('\nEnter local WAV / FLAC / MP3 file path:\n> ')
                saved, error = run_live(raw, folder, settings)
            if error:
                print(f'LIVE SESSION FAILED\nReason: {error}\nJSON: {saved or "not saved"}', flush=True)
                return 1
            print(f'LIVE SESSION FINISHED\nJSON:\n{saved}', flush=True)
            return 0
        except Exception as error:
            print(f'LIVE ANALYSIS FAILED\nReason: {error}', flush=True)
            return 1

    try:
        if args.list_loopback_devices:
            try:
                from loopback_engine import list_loopback_devices
                for device in list_loopback_devices():
                    print(f"{device['index']}: {device['name']} ({device['sampleRate']:.0f} Hz)"+
                          (' [default]' if device['default'] else ''), flush=True)
                return 0
            except Exception as error:
                print(f'Cannot list loopback devices: {error}', flush=True)
                return 1
        if args.wav or args.action:
            return dispatch(args.action or 'video')
        while True:
            print('\n'+'='*42+'\n       SUPER HAPPY SPACE 2.0\n         MUSIC ANALYSER\n'+'='*42+
                  '\n\nSelect a mode:\n\n[1] Offline Analysis\n    Extract features and export JSON.\n'
                  '\n[2] Live Analysis\n    Capture Windows system audio in real time (no file needed).\n'
                  '\n[3] Video Export\n    Generate an ECG-style MP4 with song audio.\n\n[4] Exit', flush=True)
            answer = input('\nEnter your choice:\n> ').strip()
            if answer == '4':
                return 0
            if answer not in ('1', '2', '3'):
                print('Please enter 1, 2, 3 or 4.', flush=True)
                continue
            dispatch({'1': 'offline', '2': 'live', '3': 'video'}[answer])
    except (EOFError, KeyboardInterrupt):
        print('\nMusic analyser closed.', flush=True)
        return 130


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
