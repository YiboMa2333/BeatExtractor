# Project-local outputs and captured-audio ECG video

All application modes now save to **`download/` inside this project**, regardless
of the folder from which the BAT was launched. The former Windows Downloads
location is no longer the default. Existing files in Windows Downloads are not
moved or deleted. `--output-dir` remains an explicit override for batch jobs/tests.

For **Live Analysis / system-audio capture**:

1. Run `run_beat_tracker.bat` and choose **2**.
2. Play music through the selected Windows speaker/headphone output.
3. The live window starts **stopped**. Click **Start recording** when ready, then
   **Stop recording** when finished (or close while recording).
4. The application saves and validates **`download/system_audio_live_features.json` first**.
5. It then renders **`download/system_audio_live_ecg.mp4`**, using the recorded
   Windows audio and the onset/estimated-beat events from that exact JSON.

Numbered filenames preserve existing outputs. JSON and MP4 numbering is
independent. Wait for the rendering/validation message before closing the CMD
process; the live window remains responsive and shows rendering progress. A
window-close request stops capture, then waits for JSON/video finalisation.

The MP4 contains H.264 ECG visuals and AAC audio. Preview defaults to 720p/24 FPS;
`run_beat_tracker.bat --quality`, followed by option 2, uses 1080p/30 FPS. Yellow
peaks are recorded onsets; cyan peaks are recorded estimated beats after tempo
warm-up. Peaks brighten and grow when passing the vertical bar. No second beat
detector is run during video export and no beat timestamps are fabricated.
The button returns to **Start recording** after export, allowing a fresh recording
in the same window. No recording files are created if you close before starting.

## Recording, timing and memory

A separate bounded audio queue/writer records the exact received WASAPI float32
PCM to a temporary RF64 WAV. Neither disk writing nor rendering occurs in the
capture callback. RF64 supports long recordings beyond ordinary WAV's 4GB limit.
The backend's optional programmatic analysis-pause support retains continuous
audio and marks a feature gap; the normal loopback UI now uses only Start/Stop
recording. These controls do not control music in other applications.

The video uses `frame / fps` on the same received-sample timeline as the recorded
audio and JSON. Frames stream directly to FFmpeg, with no PNG-per-frame files.
The live JSON reader streams continuous measurements and retains only the visible
event window. Video duration rounds up to a full frame, so it can extend by less
than one frame past the audio; the AAC source duration is not stretched.

If the audio writer cannot keep up, the known missing interval is filled with
silence to preserve timing, and lost-buffer/silence counts are recorded under
`diagnostics.audioRecording` and warned in the GUI. Hardware capture overflows
with unknown loss remain separately reported; no unknown sample count is invented.
This offline video export does not establish the IXN 25 ms live-alignment target.

## JSON is retained if video fails

JSON publication precedes FFmpeg startup. If FFmpeg is missing, encoding fails,
or disk space is insufficient, the completed JSON remains available. The temporary
capture audio is retained in `download/.capture-audio-*/captured.wav` (or under
your explicit output directory). Its exact path is in JSON
`capture.temporaryAudioPath`; this field is a recovery location, not a permanent
asset. The temporary audio is removed only after the MP4 is successfully validated.

After fixing the error, retry using the retained JSON and audio:

```bat
.venv\Scripts\python.exe src\live_export.py "download\system_audio_live_features.json" "download\.capture-audio-EXAMPLE\captured.wav"
```

Replace the example paths with those from your session. FFmpeg/FFprobe installation
is described in README.md. An empty capture still saves its JSON but cannot
produce a nonempty music video. Temporary capture directories and generated files
under `download/` are excluded from Git; only `download/.gitkeep` is tracked.
