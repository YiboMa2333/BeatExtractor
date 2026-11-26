"""Interactive Windows console entry point."""
try:
    from analyse import analyse, downloads_folder, save_result, validate_path
except ImportError as error:
    print(f'[ERROR] Missing dependency: {error}', flush=True)
    print('Install dependencies: python -m pip install -r requirements.txt', flush=True)
    raise SystemExit(1)


def progress(percent, message):
    print(f'[{percent:3d}% ] {message}', flush=True)
    if percent in (0, 25, 45, 65):
        print('Processing audio... this stage may take some time.', flush=True)


def main():
    print('=' * 42 + '\n   SUPER HAPPY SPACE 2.0\n       WAV BEAT TRACKER\n' + '=' * 42, flush=True)
    try:
        while True:
            try:
                path = validate_path(input('\nEnter the full path of your WAV file:\n> '))
                print(f'\nAnalysing: {path.name}\n', flush=True)
                result = analyse(path, progress)
                saved = save_result(result, downloads_folder())
                progress(100, 'Analysis completed!')
                print('\n' + '=' * 42 + '\n       ANALYSIS SUCCESSFUL!\n' + '=' * 42)
                print(f"\nSong: {result['source']}\nEstimated BPM: {result['estimatedBpm']:.1f}"
                      f"\nDetected beats: {result['beatCount']}\nDuration: {result['durationSeconds']:.1f} seconds")
                if not result['beatCount']:
                    print('No reliable beats detected; BPM is reported as 0.')
                print(f'\nJSON saved to:\n{saved}\n\n' + '=' * 42, flush=True)
            except Exception as error:
                print('\n' + '=' * 42 + '\n        ANALYSIS FAILED\n' + '=' * 42)
                print(f'\nReason: {error}\n\nNo JSON file was saved.\nPlease check the file path and try again.', flush=True)
            while True:
                answer = input('\nWould you like to analyse another song? (Y/N): ').strip().upper()
                if answer in ('Y', 'N'):
                    break
                print('Please enter Y or N.', flush=True)
            if answer == 'N':
                return
    except (EOFError, KeyboardInterrupt):
        print('\nBeat tracker closed.', flush=True)


if __name__ == '__main__':
    main()
