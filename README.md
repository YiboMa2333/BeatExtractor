# Super Happy Space 2.0 — music analyser

Choose a mode in CMD: **[1] Offline Analysis** (WAV to features JSON),
**[2] Live Analysis** (capture Windows audio, save JSON first, then export an ECG MP4),
**[3] Video Export** (the existing feature extraction plus ECG MP4), or **[4] Exit**.
Video defaults to a fast **1280 x 720, 24 FPS preview**. No GPU, decorative engine,
microphone, cloud service, recording import or manual alignment is needed.

All modes now save into **`download/` inside this project**, rather than Windows
Downloads. For system audio, stopping capture produces
**`system_audio_live_features.json` first**, followed by **`system_audio_live_ecg.mp4`**
with the captured Windows audio and the ECG events from that JSON. See
[OUTPUTS.md](OUTPUTS.md) for the JSON-first workflow and recovery instructions.

## Live mode: setup and use

Install the optional live dependencies with this folder's Python:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-live.txt
```

Use a Python installation with Tcl/Tk (standard Windows installer option).
Double-click `run_beat_tracker.bat` and choose **2**. There is **no file-path
prompt**: the GUI opens **stopped**. Open your music source, then click
**Start recording** to capture the Windows default speaker/headphone output using
WASAPI loopback. The same button becomes **Stop recording**. No microphone is
opened, and captured audio is not replayed. The Start/Stop button accompanies the
scrolling ECG, RMS, centroid, spectrum, onset
count, estimated beat count and BPM. Yellow dashed peaks are **onsets**; taller
cyan peaks are **estimated beats**. The input includes all sounds routed to the
selected Windows output endpoint, including notifications.
BPM says **Estimating...** when insufficient regular rhythm has been observed.

Click **Stop recording** (or close while recording) to incrementally finalise and validate
**`download/system_audio_live_features.json`** first (numbered when needed). Capture
does not know a song's name or EOF; timestamps are relative to the capture session.
After JSON is saved, FFmpeg renders
**`download/system_audio_live_ecg.mp4`** in the background; the live window shows
progress and remains open until rendering finishes. FFmpeg/FFprobe are required
for that MP4 step. If rendering fails, JSON and temporary audio are retained.
Offline/video modes work without live dependencies.
After export completes, the button returns to **Start recording** for a new,
independent session. It is disabled while saving/rendering. Closing the idle
window creates no recording or output files.

Live settings can be selected without changing code:

```bat
.venv\Scripts\python.exe src\cmd_app.py --action live --block-size 512 --fft-size 2048 --gui-fps 30 --queue-blocks 32
.venv\Scripts\python.exe src\cmd_app.py --list-loopback-devices
.venv\Scripts\python.exe src\cmd_app.py --action live --device 10
.venv\Scripts\python.exe src\cmd_app.py "C:\Music\song.wav" --action offline
```

For loopback, `--device INDEX` selects a WASAPI loopback index from the list command;
the example index 10 is machine-specific. Microphone endpoints are rejected. Select
a new session if you change Windows output devices. `--output-dir DIR` optionally
redirects files for tests; normal use defaults to this project's `download/`. Optional local-file
live playback is retained explicitly with `--live-source file`:

```bat
.venv\Scripts\python.exe src\cmd_app.py "C:\Music\song.wav" --action live --live-source file
```

That optional backend supports SoundFile-decoded WAV/FLAC/MP3 and uses sounddevice
output-device indices. It is not used by the default Live Analysis menu choice.

See [SYSTEM_AUDIO.md](SYSTEM_AUDIO.md) for loopback operation and timing, and
[LIVE_ANALYSIS.md](LIVE_ANALYSIS.md) for the optional local-file backend's architecture,
detector limitations, resource bounds, JSON fields and validation results. The
live estimator is a causal spectral-flux/regular-onset heuristic, not aubio or a
general-purpose neural beat tracker. Decorative engines remain independent and
are neither integrated nor installed by this live mode.

## Install and run on Windows

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/),
   enabling **Add python.exe to PATH**. Installing for your own user needs no admin rights.
2. Open this project folder in Explorer, type `cmd` into its address bar and run:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   If `.venv` already exists, only the second command is needed. Use this exact
   Python to avoid installing dependencies into another environment.
3. Install FFmpeg **and FFprobe** using the Windows-build links on the
   [official download page](https://ffmpeg.org/download.html). Choose a build with
   `libx264`. Add its `bin` folder to your user PATH and reopen CMD, or put the
   executables at `tools/ffmpeg/bin/ffmpeg.exe` and `tools/ffmpeg/bin/ffprobe.exe`.
   These tools are already installed on the development machine; pip does not install them.
4. Double-click **`run_beat_tracker.bat`**, choose **3** for Video Export, and paste a path such as
   `"C:\Users\Student\Music\my song.wav"` and press Enter. Spaces and quotes work.
   After success or failure, enter Y to export another file or N to return to the
   mode menu; choose 4 to exit. Invalid input is asked again.

Optional high-quality mode from CMD:

```bat
run_beat_tracker.bat --quality
```

Video Export and captured-audio export both use **1920 x 1080, 30 FPS** with that option, with a slower encoding preset. For a single
scripted run without the retry question:

```bat
.venv\Scripts\python.exe src\cmd_app.py "C:\Music\song.wav"
.venv\Scripts\python.exe src\cmd_app.py "C:\Music\song.wav" --mode quality
```

`--output-dir DIR` optionally redirects outputs for testing/batch jobs; normal use
uses the `download` folder beside this project's README and launcher. Missing folders are created and access
errors reported. Existing files receive numbered alternatives, never silent
overwrites. WAV inputs are never modified.

## JSON and feature meanings

The schema contains `source`, `analysisMethod` (`librosa.offline.features.v1`),
`durationSeconds`, `estimatedBpm`, `beatCount`, `analysisParameters`, and a `beats`
array. Beat indices start at 1. Each beat object has these measured descriptors:

| Field | Meaning and units |
| --- | --- |
| `beatIndex` | Sequential detected beat number, starting at 1 |
| `timeSeconds` | Time from WAV start, on the librosa analysis frame grid |
| `amplitude` | RMS of the centered, unwindowed 2048-sample mono audio frame |
| `loudnessDb` | `20 * log10(amplitude)`, relative to digital full scale 1.0; not LUFS or sound-pressure level |
| `timbreCentroid` | Magnitude-weighted spectral centroid in Hz, a rough brightness descriptor |
| `timbreBandwidth` | Magnitude-weighted spectral spread around the centroid, order 2, in Hz |
| `beatStrength` | Raw onset-strength envelope value at that beat frame; not a probability or a 0–1 score |

Analysis uses mono 22050 Hz audio and a 512-sample hop throughout. Each detected
frame selects the same centered sample window and onset entry. The extra spectral
features are computed **only at detected beat frames, in batches of 128**, using
one Hann-window spectrum per selected frame for both centroid and bandwidth.
This is equivalent to selecting those columns of the full-song feature arrays,
without calculating unused spectral columns. RMS uses the unwindowed samples,
not Hann-attenuated spectral RMS. Out-of-range window edges are zero-padded.

If RMS is zero, `loudnessDb` is `null` because log(0) is undefined. If spectral
energy is absent/negligible, centroid and bandwidth are `null`: reporting a
frequency of 0 would imply a measurement that does not exist. No NaN or Infinity
is written. The code explains these cases in `beat_features()`. Silence or fewer
than two usable detected beats produces BPM 0 and an empty beat array. No missing
beats or features are invented.

## How the ECG maps features

One cyan baseline is always visible. Beat spikes scroll past a vertical playback bar;
a progress indicator and filename/BPM/time metadata show the current position.
The fixed title is **Super Happy Space 2.0**. This is a musical beat chart inspired
by ECG shapes, not a medical ECG or a recording of heart activity.

For display, onset strength and RMS are normalised by their respective maximum
values among detected beats in this song. Intensity is `0.8 * strength + 0.2 * RMS`.
Spike height is `12 + 78 * sqrt(intensity)` pixels at 720p, scaled with resolution.
If usable onset strength is absent, RMS controls height; if both are unavailable,
the detected event gets a minimum-height marker. Centroid adds a small cyan hue
shift. These display choices do not alter the raw JSON descriptors or invent
timestamps. Normalisation is relative within a song, not an absolute cross-song
loudness comparison.

As each peak crosses the vertical bar it grows up to 45% taller, brightens toward
white-cyan, and gains a thicker glow. A short 120 ms envelope on each side makes
the emphasis rise and fade smoothly, with its maximum on the mapped beat frame.

Video frame `n` is rendered at `n / fps`. JSON is re-read for export and is the sole
beat/feature source. Beat timestamps map to the nearest available video frame;
half-frame ties round up and end-of-song beats clamp to the last frame. The program
reports actual maximum/mean timing errors. At 24 FPS the usual rounding bound is
20.833 ms; at 30 FPS it is 16.667 ms, with end-clamping potentially approaching a
full frame. There are no wall-clock animation timers.

## Performance and validation

- Static text/panel artwork is cached once. Frames use RGB, simple line strokes,
  and no whole-frame blur, procedural shader, GPU probe or PNG-per-frame files.
- Frames stream directly to FFmpeg. Preview uses x264 `ultrafast`/CRF 23; quality
  uses `veryfast`/CRF 20. Only a few video frames are held in memory.
- Tempo estimation occurs once. Feature batches avoid additional full-song
  centroid/bandwidth matrices. The inherited librosa onset analysis still loads
  the decoded mono audio and its working arrays into RAM.
- The console reports measured analysis/JSON and render/verification times,
  excluding time spent entering the path. First-use imports/JIT can be slower;
  performance varies with song length, CPU, disk and encoder build.

Measured on the development laptop, the eight-second example took **3.35 s** for
analysis/JSON and **0.86 s** for rendering plus verification (**4.21 s** measured
pipeline time, excluding interpreter/module startup). This is one local run,
not a guaranteed speed for longer songs or other computers.

Progress percentages represent real completed stages and rendered frame counts.
FFmpeg's own encoded-frame/mux-time reports are displayed separately. 100% appears
only after both output files can be reopened, the feature schema/times checked,
FFprobe confirms H.264/AAC, dimensions, frame rate and duration, and FFmpeg fully
decodes the MP4. Original WAV audio is the sole audio source, encoded as lossy AAC.
No duplicate/captured audio is mixed. Video duration allows one frame plus 25 ms
for AAC/container rounding; audio is not stretched to fit a different clock.

On video failure, the completed features JSON is preserved and its path printed.
Incomplete final MP4 placeholders are removed; temporary video/log/timing files
remain at the reported `.beat-render-*` path for diagnosis. They are cleaned only
after success. Insufficient space, missing tools and invalid/unreadable files are
reported as errors.

## Tests, example and limitations

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tools\make_example.py --output-dir examples-new
```

Tests check synthetic 120 BPM input, feature-window equivalence to full librosa
arrays, undefined features, silence, original-file preservation, Y/N retries,
JSON validation, real preview/quality exports, encoded spike pixels, AAC source
frequency, durations, collisions and failure cleanup. A real Windows BAT test
creates uniquely named test outputs in the project's `download/` and removes only those outputs.
FFmpeg-dependent tests explicitly skip when FFmpeg/FFprobe are absent.

See [the current example](examples/README.md) for generated feature/ECG outputs.

Beat estimates can miss beats or find half/double tempo, especially with changing
tempo or weak percussion. Frame windows are about 92.9 ms long with a 23.2 ms
analysis hop, so features describe a local neighbourhood rather than an isolated
instrument. Stereo downmixing can cancel out-of-phase content. Centroid/bandwidth
do not identify instruments; RMS dB is not perceptual loudness. Frame quantisation
and analysis errors are separate; this offline prototype does not claim the IXN
25 ms real-time latency target. MP4 uses a dark opaque background, not alpha.

## Later MilkDrop-style live integration

Keep this feature schema as the shared data contract. A future projectM/OpenGL
backend can consume playback-aligned PCM and use these descriptors for preset or
intensity control, while retaining the same JSON-driven foreground. Drive live
timing from the audio device's playback sample counter; buffer timestamps and
measure capture/render latency and drift separately. Add that renderer as an
optional backend, retaining this CPU ECG exporter for laptops and testing.
No MilkDrop/projectM effects are used or required by the exporter or live analyser.
`PROJECTM_INTEGRATION.md` records the earlier investigation; the old background
and recording adapter modules remain historical, outside the active code path.
