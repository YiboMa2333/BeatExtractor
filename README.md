# Super Happy Space 2.0 WAV beat tracker

This is an **offline WAV beat-tracking prototype**, not a real-time tracker.
It does not establish or claim the IXN project's 25 ms synchronisation target.
No existing beat-tracking Python source was present in this workspace when this
folder was added; it uses the requested standard librosa pipeline.

## First-time Windows setup

1. Install Python 3.11 or newer from https://www.python.org/downloads/windows/.
   Enable **Add python.exe to PATH** in the installer. Administrator access is
   not needed when installing for your own user.
2. Open this folder in File Explorer. Type `cmd` into its address bar and press
   Enter. Run these commands, one line at a time:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Double-click `run_beat_tracker.bat`. It prefers `.venv`, then `venv` in this
   folder, then the Python launcher, then Python on PATH.
4. Paste the full WAV path and press Enter. Spaces and surrounding double quotes
   are accepted, for example `"C:\Users\Student\Music\my song.wav"`.
5. Read the result and enter Y to analyse another file or N to close. After an
   error, enter Y to retry. Ctrl+C also closes the application.

The JSON is saved in your Windows Downloads folder as `song_beats.json`.
Redirected Downloads locations are resolved with Windows `SHGetKnownFolderPath`.
If that API is unavailable, the fallback is `Downloads` inside your user home
folder. A missing directory is created; access failures are reported. Existing
files are preserved by using names such as `song_beats (1).json`.

## Understanding the result

BPM means beats per minute: the estimated musical pulse. Beat timestamps are
positions in seconds from the start of the decoded audio. JSON contains `source`,
`analysisMethod`, `estimatedBpm`, `durationSeconds`, `beatCount`, and
`beatTimesSeconds`. The source WAV is only read, never edited.

Analysis loads mono audio at 22050 Hz and uses a consistent 512-sample hop for
onset strength, tempo, beat detection, and timestamp conversion. Percentages
represent completed stages, not measured elapsed work. A stage may take time,
especially on the first run while numerical routines initialise. 100% appears
only after writing, closing, reopening, and checking the JSON.

Silence or insufficient detected beats produces an empty timestamp list and BPM
0. Other results remain estimates: weak percussion, changing tempo, background
noise, and complex rhythms can cause missed beats or half/double tempo estimates.
The tracker cannot guarantee musical correctness or synchronisation accuracy.

## Tests

From this folder run:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests generate a 120 BPM pulse WAV and silence, check quoted paths with spaces,
invalid/missing/empty files, collision handling, directory creation and denied
access, JSON verification failure cleanup, Y/N input, progress, and source-file
preservation. They use temporary folders instead of your actual Downloads.
